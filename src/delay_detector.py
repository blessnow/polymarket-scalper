import asyncio
import logging
import math
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class Signal(Enum):
    HOME_WIN = "HOME_WIN"
    AWAY_WIN = "AWAY_WIN"
    NEUTRAL = "NEUTRAL"


@dataclass
class ArbitrageOpportunity:
    game_id: str
    sport: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    real_prob_home: float
    polymarket_price_home: float
    price_gap: float  # |real_prob - polymarket_price|
    signal: Signal
    confidence: float
    timestamp: datetime


class WinProbabilityModel:
    """Estimate win probability from score differential and game clock."""

    # Typical lead standard deviation by sport (used to normalize score_diff)
    # NBA: ~11 points, NHL: ~1.5 goals, MLB: ~2.5 runs
    LEAD_STD = {
        "nba": 11.0,
        "nhl": 1.5,
        "mlb": 2.5,
    }

    @classmethod
    def calculate(
        cls,
        sport: str,
        home_score: int,
        away_score: int,
        period: int,
        total_periods: int,
        time_remaining_seconds: float,
    ) -> float:
        """Return probability that home team wins [0, 1].

        Uses a simple model: normalize the lead by typical volatility * remaining uncertainty.
        Late-game leads are much more predictive than early-game leads.
        """
        score_diff = home_score - away_score

        # Game progress: 0 = start, 1 = final
        period_seconds = cls._period_seconds(sport)
        if period_seconds > 0 and time_remaining_seconds > 0:
            total_game_seconds = total_periods * period_seconds
            elapsed = total_game_seconds - time_remaining_seconds
            progress = min(elapsed / total_game_seconds, 0.99)
        else:
            progress = min(period / max(total_periods, 1), 0.99)

        progress = max(progress, 0.01)

        # Remaining uncertainty shrinks as game progresses
        # At start (progress=0.01): full std applies
        # At end (progress=0.99): almost no uncertainty
        remaining_factor = math.sqrt(1 - progress)
        lead_std = cls.LEAD_STD.get(sport, 3.0)

        # Z-score: how many standard deviations is the lead, adjusted for remaining time
        z = (score_diff / lead_std) / max(remaining_factor, 0.05)

        # Sigmoid with moderate sensitivity
        # z=0 → 0.5, z=1 → 0.73, z=2 → 0.88, z=3 → 0.95
        prob = 1.0 / (1.0 + math.exp(-1.2 * z))

        # Home advantage
        home_bonus = {"nba": 0.025, "nhl": 0.04, "mlb": 0.025}.get(sport, 0.03)
        prob += home_bonus

        return max(0.01, min(0.99, prob))

    @classmethod
    def _period_seconds(cls, sport: str) -> float:
        return {"nba": 720, "nhl": 1200, "mlb": 0}.get(sport, 600)


class SportsDelayDetector:
    """Detect price gaps between real win probability and Polymarket prices."""

    def __init__(
        self,
        min_price_gap: float = 0.05,
        min_confidence: float = 0.6,
        cooldown_seconds: float = 30.0,
    ):
        self.min_price_gap = min_price_gap
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds

        # Track last opportunity time per game to avoid spam
        self._last_opportunity: Dict[str, datetime] = {}
        # Track recent price gaps for confidence calculation
        self._gap_history: Dict[str, List[float]] = {}

    def check_opportunity(
        self,
        game_id: str,
        sport: str,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
        period: int,
        time_remaining_seconds: float,
        polymarket_price_home: float,
    ) -> Optional[ArbitrageOpportunity]:
        total_periods = {"nba": 4, "nhl": 3, "mlb": 9}.get(sport, 4)

        real_prob = WinProbabilityModel.calculate(
            sport=sport,
            home_score=home_score,
            away_score=away_score,
            period=period,
            total_periods=total_periods,
            time_remaining_seconds=time_remaining_seconds,
        )

        price_gap = real_prob - polymarket_price_home

        if abs(price_gap) < self.min_price_gap:
            return None

        # Cooldown check
        now = datetime.now()
        last = self._last_opportunity.get(game_id)
        if last and (now - last).total_seconds() < self.cooldown_seconds:
            return None

        signal = Signal.HOME_WIN if price_gap > 0 else Signal.AWAY_WIN

        confidence = self._calculate_confidence(
            game_id=game_id,
            price_gap=price_gap,
            period=period,
            total_periods=total_periods,
            polymarket_price=polymarket_price_home,
            real_prob=real_prob,
        )

        if confidence < self.min_confidence:
            return None

        self._last_opportunity[game_id] = now

        opp = ArbitrageOpportunity(
            game_id=game_id,
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            home_score=home_score,
            away_score=away_score,
            period=period,
            real_prob_home=real_prob,
            polymarket_price_home=polymarket_price_home,
            price_gap=price_gap,
            signal=signal,
            confidence=confidence,
            timestamp=now,
        )

        logger.info(
            f"Opportunity: {away_team}@{home_team} {home_score}-{away_score} Q{period} "
            f"real={real_prob:.2f} poly={polymarket_price_home:.2f} "
            f"gap={price_gap:+.3f} signal={signal.value} conf={confidence:.2f}"
        )

        return opp

    def _calculate_confidence(
        self,
        game_id: str,
        price_gap: float,
        period: int,
        total_periods: int,
        polymarket_price: float,
        real_prob: float,
    ) -> float:
        # Base confidence from gap magnitude
        base = min(abs(price_gap) / self.min_price_gap, 2.0) / 2.0

        # Higher confidence late in game (scores are more predictive)
        game_progress = period / max(total_periods, 1)
        late_bonus = game_progress * 0.3

        # Extreme prices are harder to move - lower confidence
        if polymarket_price > 0.9 or polymarket_price < 0.1:
            base *= 0.7

        # Very close to 50/50 - market is efficient here
        if 0.45 < polymarket_price < 0.55 and abs(price_gap) < 0.08:
            base *= 0.8

        confidence = min(base + late_bonus, 1.0)

        # Track gap history for trend-based confidence boost
        if game_id not in self._gap_history:
            self._gap_history[game_id] = []
        self._gap_history[game_id].append(price_gap)
        if len(self._gap_history[game_id]) > 20:
            self._gap_history[game_id].pop(0)

        # If gap is consistently in same direction, boost confidence
        gaps = self._gap_history[game_id]
        if len(gaps) >= 3:
            same_sign = sum(1 for g in gaps[-3:] if (g > 0) == (price_gap > 0))
            if same_sign >= 3:
                confidence = min(confidence * 1.15, 1.0)

        return confidence


class SportsScalper:
    """Execute trades based on sports delay opportunities."""

    def __init__(
        self,
        delay_detector: SportsDelayDetector,
        risk_per_trade: float = 0.005,
        min_confidence: float = 0.6,
    ):
        self.delay_detector = delay_detector
        self.risk_per_trade = risk_per_trade
        self.min_confidence = min_confidence
        self.executed_trades: List[Dict] = []

    async def evaluate_and_execute(
        self,
        opportunity: ArbitrageOpportunity,
        position_size: float,
        home_token: str = None,
        away_token: str = None,
    ) -> Optional[Dict]:
        if opportunity.confidence < self.min_confidence:
            return None

        if opportunity.signal == Signal.HOME_WIN and opportunity.price_gap > 0:
            side = "BUY"
            token = home_token or "HOME"
            target_team = opportunity.home_team
        elif opportunity.signal == Signal.AWAY_WIN and opportunity.price_gap < 0:
            side = "BUY"
            token = away_token or "AWAY"
            target_team = opportunity.away_team
        else:
            return None

        trade = {
            "side": side,
            "token": token,
            "target_team": target_team,
            "size": position_size,
            "game_id": opportunity.game_id,
            "real_prob": opportunity.real_prob_home,
            "polymarket_price": opportunity.polymarket_price_home,
            "price_gap": opportunity.price_gap,
            "confidence": opportunity.confidence,
            "timestamp": opportunity.timestamp,
        }

        logger.info(
            f"Trade signal: {side} {target_team} "
            f"gap={opportunity.price_gap:+.3f} "
            f"conf={opportunity.confidence:.2f} "
            f"size={position_size:.2f}"
        )

        self.executed_trades.append(trade)
        return trade

    def should_skip(self, liquidity: float, daily_pnl: float, daily_cap: float) -> bool:
        if liquidity < 100:
            logger.info("Skipping: liquidity too thin")
            return True
        if daily_pnl <= daily_cap:
            logger.info(f"Skipping: daily cap reached ({daily_pnl:.2%})")
            return True
        return False
