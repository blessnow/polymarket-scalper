import asyncio
import logging
import os
import threading
from typing import Optional
from datetime import datetime

from dotenv import load_dotenv

from binance_ws import BinanceWebSocket
from polymarket_clob import PolymarketCLOB
from delay_detector import DelayDetector, Scalper, Signal
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


class PolymarketScalper:
    def __init__(self):
        self.binance_ws = BinanceWebSocket(
            symbol="btcusdt",
            on_price_update=self._on_binance_price
        )
        
        self.polymarket = PolymarketCLOB(
            api_key=os.getenv("POLYMARKET_API_KEY"),
            api_secret=os.getenv("POLYMARKET_API_SECRET")
        )
        
        self.delay_detector = DelayDetector(
            min_spread=float(os.getenv("MIN_SPREAD_THRESHOLD", "0.003")),
            lookback_periods=12,
            convergence_threshold=0.002
        )
        
        self.scalper = Scalper(
            delay_detector=self.delay_detector,
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.005")),
            min_confidence=0.6,
            execution_delay_ms=100
        )
        
        self.risk_manager = RiskManager(
            initial_capital=1000.0,
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.005")),
            daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "0.02")),
            hard_stop_loss=float(os.getenv("HARD_STOP_LOSS", "-0.004")),
            max_position_size=float(os.getenv("MAX_POSITION_SIZE", "100"))
        )
        
        self.recorder = DataRecorder(db_path="data/scalper.db")
        
        self.trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        
        self.btc_market: Optional[dict] = None
        self.up_token: Optional[str] = None
        self.down_token: Optional[str] = None
        
        self.running = False
        self.last_binance_price = 0.0
        self.last_polymarket_price = 0.0
        self.last_check = datetime.now()
        self.last_record = datetime.now()

    async def _on_binance_price(self, trade):
        self.last_binance_price = trade.price
        self.delay_detector.update_binance_price(trade.price, trade.timestamp)

    async def initialize(self):
        logger.info("Initializing Polymarket Scalper...")
        
        await self.polymarket.connect()
        
        markets = await self.polymarket.get_markets()
        logger.info(f"Found {len(markets)} markets")
        
        for m in markets:
            question = m.get("question", "").lower()
            if "btc" in question or "bitcoin" in question:
                if "5 min" in question or "5min" in question or "up" in question or "down" in question:
                    self.btc_market = m
                    tokens = m.get("tokens", {})
                    self.up_token = tokens.get("token_a")
                    self.down_token = tokens.get("token_b")
                    logger.info(f"Found BTC 5MIN market: {m.get('question')}")
                    break
        
        if not self.btc_market:
            logger.warning("BTC 5MIN market not found, using mock data for testing")
            self.up_token = "mock_up_token"
            self.down_token = "mock_down_token"
            self.last_polymarket_price = 0.5

    async def monitor_polymarket(self):
        logger.info("Starting Polymarket monitor...")
        
        while self.running:
            try:
                if self.up_token and self.up_token != "mock_up_token":
                    price = await self.polymarket.get_mid_price(self.up_token)
                    if price:
                        self.last_polymarket_price = price
                        self.delay_detector.update_polymarket_price(price)
                
                await asyncio.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error monitoring Polymarket: {e}")
                await asyncio.sleep(1)

    async def check_opportunities(self):
        logger.info("Starting opportunity checker...")
        
        while self.running:
            try:
                now = datetime.now()
                
                if (now - self.last_check).total_seconds() < 0.5:
                    await asyncio.sleep(0.1)
                    continue
                
                self.last_check = now
                
                if self.last_binance_price == 0 or self.last_polymarket_price == 0:
                    await asyncio.sleep(0.1)
                    continue
                
                spread = 0.0
                if self.last_binance_price > 0:
                    spread = (self.last_polymarket_price - self.last_binance_price) / self.last_binance_price
                
                if (now - self.last_record).total_seconds() >= 1.0:
                    self.recorder.record_price(
                        binance_price=self.last_binance_price,
                        polymarket_price=self.last_polymarket_price,
                        spread=spread
                    )
                    self.last_record = now
                
                klines = await self.binance_ws.get_5m_klines(limit=100)
                
                opportunity = self.delay_detector.check_opportunity(
                    binance_price=self.last_binance_price,
                    polymarket_price=self.last_polymarket_price,
                    klines=klines
                )
                
                if opportunity:
                    await self._handle_opportunity(opportunity, klines)
                
                tokens_to_close = self.risk_manager.check_hard_stop()
                for token in tokens_to_close:
                    logger.warning(f"Hard stop triggered, closing position: {token}")
                
                if (now - self.last_record).total_seconds() >= 5.0:
                    stats = self.risk_manager.get_stats_summary()
                    self.recorder.record_stats(stats)
                
            except Exception as e:
                logger.error(f"Error checking opportunities: {e}")
                await asyncio.sleep(1)

    async def _handle_opportunity(self, opportunity, klines):
        logger.info(
            f"Opportunity: Spread={opportunity.spread:.4f}, "
            f"Signal={opportunity.signal.value}, "
            f"Confidence={opportunity.confidence:.2f}"
        )
        
        position_size = self.risk_manager.calculate_position_size(
            price=opportunity.polymarket_price,
            confidence=opportunity.confidence
        )
        
        trade = await self.scalper.evaluate_and_execute(
            opportunity=opportunity,
            position_size=position_size
        )
        
        executed = False
        
        if trade:
            if not self.scalper.should_skip(
                liquidity=1000,
                daily_pnl=self.risk_manager.get_daily_pnl(),
                daily_cap=self.risk_manager.daily_loss_limit * self.risk_manager.initial_capital
            ):
                if self.dry_run:
                    logger.info(
                        f"[DRY RUN] Would execute: {trade['side']} {trade['size']} "
                        f"@ {trade['polymarket_price']:.4f}"
                    )
                    executed = True
                    
                    self._simulate_trade(trade, opportunity)
        
        self.recorder.record_opportunity(
            binance_price=opportunity.binance_price,
            polymarket_price=opportunity.polymarket_price,
            spread=opportunity.spread,
            signal=opportunity.signal.value,
            confidence=opportunity.confidence,
            executed=executed,
            position_size=position_size
        )

    def _simulate_trade(self, trade, opportunity):
        import random
        
        win_probability = 0.55 + (opportunity.confidence - 0.6) * 0.3
        
        pnl = 0
        if random.random() < win_probability:
            pnl = opportunity.spread * trade['size'] * trade['polymarket_price'] * 0.8
        else:
            pnl = -opportunity.spread * trade['size'] * trade['polymarket_price'] * 0.5
        
        self.risk_manager.current_capital += pnl
        
        self.recorder.record_trade(
            token=trade['token'],
            side=trade['side'],
            size=trade['size'],
            price=trade['polymarket_price'],
            pnl=pnl,
            status='FILLED'
        )
        
        logger.info(f"[DRY RUN] Simulated trade PnL: ${pnl:.2f}")

    async def run(self):
        self.running = True
        
        await self.initialize()
        
        tasks = [
            self.binance_ws.connect(),
            self.monitor_polymarket(),
            self.check_opportunities()
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            self.running = False
            await self.binance_ws.stop()
            await self.polymarket.close()

    def stop(self):
        self.running = False
        self.binance_ws.stop()

    def print_stats(self):
        stats = self.risk_manager.get_stats_summary()
        logger.info(
            f"Stats: Capital={stats['capital']:.2f}, "
            f"PnL={stats['total_pnl']:.2f}, "
            f"Daily={stats['daily_pnl']:.2f}, "
            f"Trades={stats['trades']}, "
            f"WinRate={stats['win_rate']:.2%}"
        )


def run_dashboard():
    from dashboard import run_dashboard as start_dashboard
    start_dashboard(host='0.0.0.0', port=8080)


async def main():
    scalper = PolymarketScalper()
    
    dashboard_thread = threading.Thread(target=run_dashboard, daemon=True)
    dashboard_thread.start()
    
    logger.info("Dashboard running at http://localhost:8080")
    
    try:
        await scalper.run()
    except KeyboardInterrupt:
        scalper.stop()
        scalper.print_stats()


if __name__ == "__main__":
    asyncio.run(main())
