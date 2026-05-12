import asyncio
import logging
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


class Signal(Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    NEUTRAL = "NEUTRAL"


@dataclass
class ArbitrageOpportunity:
    binance_price: float
    polymarket_price: float
    spread: float
    signal: Signal
    confidence: float
    timestamp: datetime


class DelayDetector:
    def __init__(
        self,
        min_spread: float = 0.003,
        lookback_periods: int = 12,
        convergence_threshold: float = 0.002
    ):
        self.min_spread = min_spread
        self.lookback_periods = lookback_periods
        self.convergence_threshold = convergence_threshold
        
        self.binance_prices: List[float] = []
        self.polymarket_prices: List[float] = []
        self.timestamps: List[datetime] = []
        
        self.max_history = 100

    def update_binance_price(self, price: float, timestamp: datetime = None):
        self.binance_prices.append(price)
        self.timestamps.append(timestamp or datetime.now())
        
        if len(self.binance_prices) > self.max_history:
            self.binance_prices.pop(0)
            self.timestamps.pop(0)

    def update_polymarket_price(self, price: float):
        self.polymarket_prices.append(price)
        
        if len(self.polymarket_prices) > self.max_history:
            self.polymarket_prices.pop(0)

    def calculate_spread(self) -> float:
        if not self.binance_prices or not self.polymarket_prices:
            return 0.0
        
        binance_price = self.binance_prices[-1]
        polymarket_price = self.polymarket_prices[-1]
        
        if binance_price == 0:
            return 0.0
        
        return (polymarket_price - binance_price) / binance_price

    def detect_signal_convergence(self, klines: List) -> Signal:
        if len(klines) < self.lookback_periods:
            return Signal.NEUTRAL
        
        closes = [float(k[4]) for k in klines[-self.lookback_periods:]]
        
        sma_short = np.mean(closes[-5:])
        sma_long = np.mean(closes[-self.lookback_periods:])
        
        rsi = self._calculate_rsi(closes)
        
        momentum = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] != 0 else 0
        
        bullish_signals = 0
        bearish_signals = 0
        
        if sma_short > sma_long:
            bullish_signals += 1
        else:
            bearish_signals += 1
        
        if rsi > 60:
            bullish_signals += 1
        elif rsi < 40:
            bearish_signals += 1
        
        if momentum > self.convergence_threshold:
            bullish_signals += 1
        elif momentum < -self.convergence_threshold:
            bearish_signals += 1
        
        if bullish_signals >= 2:
            return Signal.BULL
        elif bearish_signals >= 2:
            return Signal.BEAR
        else:
            return Signal.NEUTRAL

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi

    def check_opportunity(
        self,
        binance_price: float,
        polymarket_price: float,
        klines: List
    ) -> Optional[ArbitrageOpportunity]:
        self.update_binance_price(binance_price)
        self.update_polymarket_price(polymarket_price)
        
        spread = self.calculate_spread()
        
        if abs(spread) < self.min_spread:
            return None
        
        signal = self.detect_signal_convergence(klines)
        
        if signal == Signal.NEUTRAL:
            return None
        
        confidence = self._calculate_confidence(spread, signal, klines)
        
        return ArbitrageOpportunity(
            binance_price=binance_price,
            polymarket_price=polymarket_price,
            spread=spread,
            signal=signal,
            confidence=confidence,
            timestamp=datetime.now()
        )

    def _calculate_confidence(self, spread: float, signal: Signal, klines: List) -> float:
        base_confidence = min(abs(spread) / self.min_spread, 1.0)
        
        if len(klines) >= 12:
            closes = [float(k[4]) for k in klines[-12:]]
            volatility = np.std(closes) / np.mean(closes)
            
            if volatility < 0.01:
                base_confidence *= 1.2
            elif volatility > 0.03:
                base_confidence *= 0.8
        
        return min(base_confidence, 1.0)


class Scalper:
    def __init__(
        self,
        delay_detector: DelayDetector,
        risk_per_trade: float = 0.005,
        min_confidence: float = 0.6,
        execution_delay_ms: int = 100
    ):
        self.delay_detector = delay_detector
        self.risk_per_trade = risk_per_trade
        self.min_confidence = min_confidence
        self.execution_delay_ms = execution_delay_ms
        
        self.pending_orders: Dict[str, Dict] = {}
        self.executed_trades: List[Dict] = []

    async def evaluate_and_execute(
        self,
        opportunity: ArbitrageOpportunity,
        position_size: float
    ) -> Optional[Dict]:
        if opportunity.confidence < self.min_confidence:
            logger.info(f"Confidence too low: {opportunity.confidence:.2f}")
            return None
        
        if opportunity.signal == Signal.BULL and opportunity.spread < 0:
            side = "BUY"
            target_token = "UP"
        elif opportunity.signal == Signal.BEAR and opportunity.spread > 0:
            side = "BUY"
            target_token = "DOWN"
        else:
            logger.info("Signal and spread mismatch, skipping")
            return None
        
        trade = {
            "side": side,
            "token": target_token,
            "size": position_size,
            "binance_price": opportunity.binance_price,
            "polymarket_price": opportunity.polymarket_price,
            "spread": opportunity.spread,
            "confidence": opportunity.confidence,
            "timestamp": opportunity.timestamp
        }
        
        logger.info(
            f"Opportunity detected: {side} {target_token} "
            f"Spread: {opportunity.spread:.4f} "
            f"Confidence: {opportunity.confidence:.2f}"
        )
        
        return trade

    def should_skip(self, liquidity: float, daily_pnl: float, daily_cap: float) -> bool:
        if liquidity < 100:
            logger.info("Skipping: liquidity too thin")
            return True
        
        if daily_pnl <= daily_cap:
            logger.info(f"Skipping: daily cap reached ({daily_pnl:.2%})")
            return True
        
        return False
