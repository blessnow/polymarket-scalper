import sqlite3
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
import os

logger = logging.getLogger(__name__)


@dataclass
class PriceRecord:
    timestamp: datetime
    binance_price: float
    polymarket_price: float
    spread: float


@dataclass
class OpportunityRecord:
    timestamp: datetime
    binance_price: float
    polymarket_price: float
    spread: float
    signal: str
    confidence: float
    executed: bool
    position_size: float
    pnl: float = 0.0


@dataclass
class TradeRecord:
    timestamp: datetime
    token: str
    side: str
    size: float
    price: float
    pnl: float
    status: str


class DataRecorder:
    def __init__(self, db_path: str = "data/scalper.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                binance_price REAL NOT NULL,
                polymarket_price REAL NOT NULL,
                spread REAL NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                binance_price REAL NOT NULL,
                polymarket_price REAL NOT NULL,
                spread REAL NOT NULL,
                signal TEXT NOT NULL,
                confidence REAL NOT NULL,
                executed BOOLEAN NOT NULL,
                position_size REAL NOT NULL,
                pnl REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                token TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                price REAL NOT NULL,
                pnl REAL NOT NULL,
                status TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                capital REAL NOT NULL,
                total_pnl REAL NOT NULL,
                daily_pnl REAL NOT NULL,
                trades INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                open_positions INTEGER NOT NULL
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_prices_time ON prices(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_opps_time ON opportunities(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(timestamp)')
        
        conn.commit()
        conn.close()
        
        logger.info(f"Database initialized: {self.db_path}")

    def record_price(self, binance_price: float, polymarket_price: float, spread: float):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO prices (timestamp, binance_price, polymarket_price, spread)
            VALUES (?, ?, ?, ?)
        ''', (datetime.now(), binance_price, polymarket_price, spread))
        
        conn.commit()
        conn.close()

    def record_opportunity(
        self,
        binance_price: float,
        polymarket_price: float,
        spread: float,
        signal: str,
        confidence: float,
        executed: bool,
        position_size: float,
        pnl: float = 0.0
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO opportunities 
            (timestamp, binance_price, polymarket_price, spread, signal, confidence, executed, position_size, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now(), binance_price, polymarket_price, spread, signal, confidence, executed, position_size, pnl))
        
        conn.commit()
        conn.close()

    def record_trade(self, token: str, side: str, size: float, price: float, pnl: float, status: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO trades (timestamp, token, side, size, price, pnl, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now(), token, side, size, price, pnl, status))
        
        conn.commit()
        conn.close()

    def record_stats(self, stats: Dict):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO stats (timestamp, capital, total_pnl, daily_pnl, trades, win_rate, open_positions)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(),
            stats.get('capital', 0),
            stats.get('total_pnl', 0),
            stats.get('daily_pnl', 0),
            stats.get('trades', 0),
            stats.get('win_rate', 0),
            stats.get('open_positions', 0)
        ))
        
        conn.commit()
        conn.close()

    def get_recent_prices(self, limit: int = 100) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT timestamp, binance_price, polymarket_price, spread
            FROM prices
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'timestamp': row[0],
                'binance_price': row[1],
                'polymarket_price': row[2],
                'spread': row[3]
            }
            for row in reversed(rows)
        ]

    def get_recent_opportunities(self, limit: int = 50) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT timestamp, binance_price, polymarket_price, spread, signal, confidence, executed, position_size, pnl
            FROM opportunities
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'timestamp': row[0],
                'binance_price': row[1],
                'polymarket_price': row[2],
                'spread': row[3],
                'signal': row[4],
                'confidence': row[5],
                'executed': bool(row[6]),
                'position_size': row[7],
                'pnl': row[8]
            }
            for row in reversed(rows)
        ]

    def get_recent_trades(self, limit: int = 50) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT timestamp, token, side, size, price, pnl, status
            FROM trades
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'timestamp': row[0],
                'token': row[1],
                'side': row[2],
                'size': row[3],
                'price': row[4],
                'pnl': row[5],
                'status': row[6]
            }
            for row in rows
        ]

    def get_stats_summary(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM opportunities')
        total_opportunities = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM opportunities WHERE executed = 1')
        executed_opportunities = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM trades')
        total_trades = cursor.fetchone()[0]
        
        cursor.execute('SELECT SUM(pnl) FROM trades')
        total_pnl = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM trades WHERE pnl > 0')
        winning_trades = cursor.fetchone()[0]
        
        cursor.execute('SELECT AVG(spread) FROM opportunities')
        avg_spread = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT AVG(confidence) FROM opportunities')
        avg_confidence = cursor.fetchone()[0] or 0
        
        cursor.execute('''
            SELECT timestamp, capital, total_pnl, daily_pnl, trades, win_rate, open_positions
            FROM stats
            ORDER BY timestamp DESC
            LIMIT 1
        ''')
        latest_stats = cursor.fetchone()
        
        conn.close()
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        return {
            'total_opportunities': total_opportunities,
            'executed_opportunities': executed_opportunities,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_spread': avg_spread,
            'avg_confidence': avg_confidence,
            'latest_stats': {
                'timestamp': latest_stats[0] if latest_stats else None,
                'capital': latest_stats[1] if latest_stats else 1000,
                'total_pnl': latest_stats[2] if latest_stats else 0,
                'daily_pnl': latest_stats[3] if latest_stats else 0,
                'trades': latest_stats[4] if latest_stats else 0,
                'win_rate': latest_stats[5] if latest_stats else 0,
                'open_positions': latest_stats[6] if latest_stats else 0
            } if latest_stats else None
        }

    def get_pnl_history(self, hours: int = 48) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT timestamp, total_pnl, daily_pnl
            FROM stats
            WHERE timestamp >= datetime('now', ?)
            ORDER BY timestamp ASC
        ''', (f'-{hours} hours',))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                'timestamp': row[0],
                'total_pnl': row[1],
                'daily_pnl': row[2]
            }
            for row in rows
        ]
