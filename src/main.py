import asyncio
import logging
import os
import threading
import re
from typing import Optional, Dict, List
from datetime import datetime

from dotenv import load_dotenv

from sports_feed import SportsFeed, ScoreEvent, LiveGame
from polymarket_clob import PolymarketCLOB
from delay_detector import SportsDelayDetector, SportsScalper, Signal
from risk_manager import RiskManager
from data_recorder import DataRecorder

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scalper.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Period durations in seconds for time_remaining parsing
PERIOD_SECONDS = {
    "nba": 720,   # 12 min quarters
    "nhl": 1200,  # 20 min periods
    "mlb": 0,     # no clock
}


def parse_time_remaining(time_str: str, sport: str) -> float:
    """Parse ESPN time string like '7:32' or '0:00' to seconds."""
    if not time_str or sport == "mlb":
        return 0.0
    try:
        parts = time_str.split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except (ValueError, IndexError):
        pass
    return 0.0


def extract_team_abbr(text: str) -> str:
    """Extract potential team abbreviation from question text."""
    # Try patterns like "LAL" or "BOS" (2-4 uppercase letters)
    match = re.search(r'\b([A-Z]{2,4})\b', text)
    return match.group(1) if match else ""


class SportsScalperApp:
    def __init__(self):
        leagues = os.getenv("SPORTS_LEAGUES", "nba,nhl,mlb").split(",")
        poll_interval = float(os.getenv("SCORE_POLL_INTERVAL", "5"))

        self.sports_feed = SportsFeed(
            leagues=leagues,
            poll_interval=poll_interval,
            on_score_change=self._on_score_change,
        )

        self.polymarket = PolymarketCLOB(
            api_key=os.getenv("POLYMARKET_API_KEY"),
            api_secret=os.getenv("POLYMARKET_API_SECRET"),
        )

        self.delay_detector = SportsDelayDetector(
            min_price_gap=float(os.getenv("MIN_PRICE_GAP", "0.05")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.6")),
            cooldown_seconds=float(os.getenv("OPPORTUNITY_COOLDOWN", "30")),
        )

        self.scalper = SportsScalper(
            delay_detector=self.delay_detector,
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.005")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.6")),
        )

        self.risk_manager = RiskManager(
            initial_capital=float(os.getenv("INITIAL_CAPITAL", "1000")),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.005")),
            daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "0.02")),
            hard_stop_loss=float(os.getenv("HARD_STOP_LOSS", "-0.004")),
            max_position_size=float(os.getenv("MAX_POSITION_SIZE", "100")),
        )

        self.recorder = DataRecorder(db_path="data/scalper.db")

        self.trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

        # Map: game_key -> {home_token, away_token, polymarket_home_price, ...}
        self.game_markets: Dict[str, Dict] = {}

        self.running = False
        self.last_binance_price = 0.0  # kept for recorder compat
        self.last_polymarket_price = 0.0  # kept for recorder compat
        self.last_check = datetime.now()
        self.last_record = datetime.now()

    async def _on_score_change(self, event: ScoreEvent):
        """Callback when ESPN detects a score change."""
        game_key = f"{event.sport}:{event.game_id}"
        market = self.game_markets.get(game_key)

        if not market:
            logger.debug(f"No Polymarket market mapped for {game_key}")
            return

        poly_price_home = market.get("home_price", 0.5)
        time_remaining = parse_time_remaining(event.time_remaining, event.sport)

        opportunity = self.delay_detector.check_opportunity(
            game_id=event.game_id,
            sport=event.sport,
            home_team=event.home_team,
            away_team=event.away_team,
            home_score=event.home_score,
            away_score=event.away_score,
            period=event.period,
            time_remaining_seconds=time_remaining,
            polymarket_price_home=poly_price_home,
        )

        if opportunity:
            await self._handle_opportunity(opportunity, market)

    async def initialize(self):
        logger.info("Initializing Sports Scalper...")

        await self.polymarket.connect()

        # Find all live sports markets on Polymarket
        sports_markets = await self.polymarket.find_sports_markets()
        logger.info(f"Found {len(sports_markets)} Polymarket sports markets")

        for m in sports_markets:
            if m.get("live"):
                sport = m.get("sport", "")
                question = m.get("question", "")
                logger.info(f"Live market: [{sport}] {question}")

                # Try to match with live games by team names
                self.game_markets[f"poly:{m['market_id']}"] = {
                    "home_token": m["home_token"],
                    "away_token": m["away_token"],
                    "home_price": 0.5,
                    "away_price": 0.5,
                    "question": question,
                    "sport": sport,
                }

        logger.info(f"Mapped {len(self.game_markets)} live markets")

    async def _match_games_to_markets(self):
        """Match ESPN live games to Polymarket markets by team abbreviations."""
        live_games = self.sports_feed.get_live_games()

        for game in live_games:
            game_key = f"{game.sport}:{game.game_id}"

            if game_key in self.game_markets:
                continue

            # Search through polymarket markets for matching teams
            for mk, market in list(self.game_markets.items()):
                if not mk.startswith("poly:"):
                    continue

                question = market.get("question", "").upper()
                home_match = game.home_team.upper() in question
                away_match = game.away_team.upper() in question

                if home_match and away_match:
                    # Move from poly: key to game_key
                    self.game_markets[game_key] = market
                    del self.game_markets[mk]
                    logger.info(
                        f"Matched game {game.away_team}@{game.home_team} to market: {market['question']}"
                    )
                    break

    async def monitor_polymarket(self):
        """Poll Polymarket prices for mapped game markets."""
        logger.info("Starting Polymarket price monitor...")

        while self.running:
            try:
                # Only poll if there are mapped markets with live games
                active_keys = []
                for game_key, market in self.game_markets.items():
                    if game_key.startswith("poly:"):
                        continue  # Skip unmatched markets
                    if not market.get("home_token"):
                        continue
                    active_keys.append(game_key)

                if not active_keys:
                    await asyncio.sleep(10)
                    continue

                for game_key in active_keys:
                    market = self.game_markets[game_key]
                    home_token = market.get("home_token")
                    away_token = market.get("away_token")

                    if home_token:
                        price = await self.polymarket.get_mid_price(home_token)
                        if price:
                            market["home_price"] = price

                    if away_token:
                        price = await self.polymarket.get_mid_price(away_token)
                        if price:
                            market["away_price"] = price

                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error monitoring Polymarket: {e}")
                await asyncio.sleep(5)

    async def periodic_check(self):
        """Periodic checks: match games, refresh markets, record data."""
        logger.info("Starting periodic checker...")

        while self.running:
            try:
                now = datetime.now()

                # Match games to markets every 30s
                await self._match_games_to_markets()

                # Refresh sports market list every 60s
                if (now - self.last_check).total_seconds() >= 60:
                    new_markets = await self.polymarket.find_sports_markets()
                    logger.info(f"Found {len(new_markets)} active sports markets")
                    # Don't flood game_markets - only add if matched to live games
                    self.last_check = now

                # Record data every 5s
                if (now - self.last_record).total_seconds() >= 5:
                    for game_key, market in self.game_markets.items():
                        game = None
                        for g in self.sports_feed.get_live_games():
                            if f"{g.sport}:{g.game_id}" == game_key:
                                game = g
                                break

                        if game:
                            self.recorder.record_price(
                                binance_price=game.home_score,
                                polymarket_price=market.get("home_price", 0.5),
                                spread=market.get("home_price", 0.5) - 0.5,
                            )

                    stats = self.risk_manager.get_stats_summary()
                    self.recorder.record_stats(stats)
                    self.last_record = now

                await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Error in periodic check: {e}")
                await asyncio.sleep(5)

    async def _handle_opportunity(self, opportunity, market: Dict):
        logger.info(
            f"Opportunity: {opportunity.away_team}@{opportunity.home_team} "
            f"{opportunity.home_score}-{opportunity.away_score} "
            f"real={opportunity.real_prob_home:.2f} "
            f"poly={opportunity.polymarket_price_home:.2f} "
            f"gap={opportunity.price_gap:+.3f} "
            f"signal={opportunity.signal.value} "
            f"conf={opportunity.confidence:.2f}"
        )

        poly_price = (
            opportunity.polymarket_price_home
            if opportunity.signal == Signal.HOME_WIN
            else 1 - opportunity.polymarket_price_home
        )

        position_size = self.risk_manager.calculate_position_size(
            price=poly_price,
            confidence=opportunity.confidence,
        )

        trade = await self.scalper.evaluate_and_execute(
            opportunity=opportunity,
            position_size=position_size,
            home_token=market.get("home_token"),
            away_token=market.get("away_token"),
        )

        executed = False

        if trade:
            if not self.scalper.should_skip(
                liquidity=1000,
                daily_pnl=self.risk_manager.get_daily_pnl(),
                daily_cap=self.risk_manager.daily_loss_limit * self.risk_manager.initial_capital,
            ):
                if self.dry_run:
                    logger.info(
                        f"[DRY RUN] Would execute: {trade['side']} {trade['target_team']} "
                        f"size={trade['size']:.2f} @ {poly_price:.4f}"
                    )
                    executed = True
                    self._simulate_trade(trade, opportunity)

        self.recorder.record_opportunity(
            binance_price=opportunity.real_prob_home,
            polymarket_price=opportunity.polymarket_price_home,
            spread=opportunity.price_gap,
            signal=opportunity.signal.value,
            confidence=opportunity.confidence,
            executed=executed,
            position_size=position_size,
        )

    def _simulate_trade(self, trade: Dict, opportunity):
        import random

        # Better confidence → higher win rate
        win_prob = 0.5 + (opportunity.confidence - 0.5) * 0.6

        pnl = 0
        gap = abs(opportunity.price_gap)
        if random.random() < win_prob:
            pnl = gap * trade['size'] * trade.get('polymarket_price', 0.5) * 0.8
        else:
            pnl = -gap * trade['size'] * trade.get('polymarket_price', 0.5) * 0.5

        self.risk_manager.current_capital += pnl

        self.recorder.record_trade(
            token=trade['token'],
            side=trade['side'],
            size=trade['size'],
            price=trade.get('polymarket_price', 0.5),
            pnl=pnl,
            status='FILLED',
        )

        logger.info(f"[DRY RUN] Simulated trade PnL: ${pnl:.2f}")

    async def run(self):
        self.running = True
        await self.initialize()

        tasks = [
            self.sports_feed.connect(),
            self.monitor_polymarket(),
            self.periodic_check(),
        ]

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.running = False
            await self.sports_feed.close()
            await self.polymarket.close()

    def stop(self):
        self.running = False
        self.sports_feed.stop()

    def print_stats(self):
        stats = self.risk_manager.get_stats_summary()
        games = self.sports_feed.get_live_games()
        logger.info(
            f"Stats: Capital=${stats['capital']:.2f}, "
            f"PnL=${stats['total_pnl']:.2f}, "
            f"Daily=${stats['daily_pnl']:.2f}, "
            f"Trades={stats['trades']}, "
            f"WinRate={stats['win_rate']:.0%}, "
            f"LiveGames={len(games)}, "
            f"Markets={len(self.game_markets)}"
        )


def run_dashboard():
    from dashboard import run_dashboard as start_dashboard
    start_dashboard(host='0.0.0.0', port=8080)


async def main():
    app = SportsScalperApp()

    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()

    logger.info("Dashboard running at http://localhost:8080")

    try:
        await app.run()
    except KeyboardInterrupt:
        app.stop()
        app.print_stats()


if __name__ == "__main__":
    asyncio.run(main())
