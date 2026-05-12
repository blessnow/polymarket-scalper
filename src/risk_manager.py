import logging
from typing import Dict, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


class RiskAction(Enum):
    ALLOW = "ALLOW"
    REDUCE_SIZE = "REDUCE_SIZE"
    BLOCK = "BLOCK"


@dataclass
class Position:
    token_id: str
    side: str
    size: float
    entry_price: float
    current_price: float
    pnl: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DailyStats:
    date: datetime
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0


class RiskManager:
    def __init__(
        self,
        initial_capital: float = 1000.0,
        risk_per_trade: float = 0.005,
        daily_loss_limit: float = 0.02,
        hard_stop_loss: float = -0.004,
        max_position_size: float = 100.0,
        max_open_positions: int = 3
    ):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.daily_loss_limit = daily_loss_limit
        self.hard_stop_loss = hard_stop_loss
        self.max_position_size = max_position_size
        self.max_open_positions = max_open_positions
        
        self.positions: Dict[str, Position] = {}
        self.daily_stats: Dict[str, DailyStats] = {}
        self.trade_history: List[Dict] = []
        
        self._init_daily_stats()

    def _init_daily_stats(self):
        today = datetime.now().date().isoformat()
        if today not in self.daily_stats:
            self.daily_stats[today] = DailyStats(date=datetime.now())

    def _get_today_stats(self) -> DailyStats:
        self._init_daily_stats()
        today = datetime.now().date().isoformat()
        return self.daily_stats[today]

    def calculate_position_size(self, price: float, confidence: float = 1.0) -> float:
        risk_amount = self.current_capital * self.risk_per_trade
        
        position_size = risk_amount / price if price > 0 else 0
        
        position_size *= confidence
        
        position_size = min(position_size, self.max_position_size)
        
        return round(position_size, 2)

    def check_risk(self, proposed_size: float, proposed_price: float) -> RiskAction:
        today_stats = self._get_today_stats()
        
        if today_stats.pnl <= self.daily_loss_limit * self.initial_capital:
            logger.warning(f"Daily loss limit reached: {today_stats.pnl:.2%}")
            return RiskAction.BLOCK
        
        if len(self.positions) >= self.max_open_positions:
            logger.warning("Max open positions reached")
            return RiskAction.BLOCK
        
        proposed_risk = proposed_size * proposed_price
        max_risk = self.current_capital * self.risk_per_trade
        
        if proposed_risk > max_risk:
            logger.info(f"Proposed size too large, reducing from {proposed_size}")
            return RiskAction.REDUCE_SIZE
        
        return RiskAction.ALLOW

    def open_position(self, token_id: str, side: str, size: float, price: float) -> bool:
        risk_action = self.check_risk(size, price)
        
        if risk_action == RiskAction.BLOCK:
            return False
        
        if risk_action == RiskAction.REDUCE_SIZE:
            size = self.calculate_position_size(price)
        
        position = Position(
            token_id=token_id,
            side=side,
            size=size,
            entry_price=price,
            current_price=price
        )
        
        self.positions[token_id] = position
        
        logger.info(f"Position opened: {side} {size} @ {price}")
        
        return True

    def close_position(self, token_id: str, exit_price: float) -> float:
        if token_id not in self.positions:
            return 0.0
        
        position = self.positions[token_id]
        
        if position.side == "BUY":
            pnl = (exit_price - position.entry_price) * position.size
        else:
            pnl = (position.entry_price - exit_price) * position.size
        
        position.pnl = pnl
        self.current_capital += pnl
        
        today_stats = self._get_today_stats()
        today_stats.trades += 1
        today_stats.pnl += pnl
        
        if pnl > 0:
            today_stats.wins += 1
        else:
            today_stats.losses += 1
        
        if today_stats.pnl > today_stats.peak_pnl:
            today_stats.peak_pnl = today_stats.pnl
        
        drawdown = today_stats.peak_pnl - today_stats.pnl
        if drawdown > today_stats.max_drawdown:
            today_stats.max_drawdown = drawdown
        
        self.trade_history.append({
            "token_id": token_id,
            "side": position.side,
            "size": position.size,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "timestamp": datetime.now()
        })
        
        del self.positions[token_id]
        
        logger.info(f"Position closed: PnL = {pnl:.4f}")
        
        return pnl

    def update_position_price(self, token_id: str, current_price: float):
        if token_id in self.positions:
            self.positions[token_id].current_price = current_price

    def check_hard_stop(self) -> List[str]:
        tokens_to_close = []
        
        for token_id, position in self.positions.items():
            if position.side == "BUY":
                pnl_pct = (position.current_price - position.entry_price) / position.entry_price
            else:
                pnl_pct = (position.entry_price - position.current_price) / position.entry_price
            
            if pnl_pct <= self.hard_stop_loss:
                logger.warning(f"Hard stop triggered for {token_id}: {pnl_pct:.2%}")
                tokens_to_close.append(token_id)
        
        return tokens_to_close

    def get_total_pnl(self) -> float:
        return self.current_capital - self.initial_capital

    def get_daily_pnl(self) -> float:
        today_stats = self._get_today_stats()
        return today_stats.pnl

    def get_open_positions_count(self) -> int:
        return len(self.positions)

    def get_stats_summary(self) -> Dict:
        today_stats = self._get_today_stats()
        
        win_rate = 0.0
        if today_stats.trades > 0:
            win_rate = today_stats.wins / today_stats.trades
        
        return {
            "capital": self.current_capital,
            "total_pnl": self.get_total_pnl(),
            "daily_pnl": today_stats.pnl,
            "trades": today_stats.trades,
            "win_rate": win_rate,
            "open_positions": len(self.positions),
            "max_drawdown": today_stats.max_drawdown
        }
