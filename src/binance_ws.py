import asyncio
import json
import logging
import ssl
from typing import Callable, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class BinanceTrade:
    price: float
    quantity: float
    timestamp: datetime
    trade_id: int


class BinanceWebSocket:
    def __init__(self, symbol: str = "btcusdt", on_price_update: Optional[Callable] = None):
        self.symbol = symbol.lower()
        self.ws_url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        self.on_price_update = on_price_update
        self.ws = None
        self.running = False
        self.last_price = 0.0
        self.price_history = []
        self.max_history = 100

    async def connect(self):
        import websockets
        
        logger.info(f"Connecting to Binance WebSocket: {self.ws_url}")
        self.running = True
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        while self.running:
            try:
                async with websockets.connect(self.ws_url, ssl=ssl_context) as ws:
                    self.ws = ws
                    logger.info("Connected to Binance WebSocket")
                    
                    while self.running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self._handle_message(message)
                        except asyncio.TimeoutError:
                            await ws.ping()
                            
            except Exception as e:
                logger.error(f"Binance WebSocket error: {e}")
                if self.running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            
            trade = BinanceTrade(
                price=float(data['p']),
                quantity=float(data['q']),
                timestamp=datetime.fromtimestamp(data['T'] / 1000),
                trade_id=data['t']
            )
            
            self.last_price = trade.price
            self.price_history.append(trade)
            
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)
            
            if self.on_price_update:
                await self.on_price_update(trade)
                
        except Exception as e:
            logger.error(f"Error handling Binance message: {e}")

    async def get_5m_klines(self, limit: int = 100) -> list:
        import aiohttp
        import ssl as ssl_module
        
        url = f"https://api.binance.com/api/v3/klines"
        params = {
            "symbol": self.symbol.upper(),
            "interval": "5m",
            "limit": limit
        }
        
        ssl_context = ssl_module.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl_module.CERT_NONE
        
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    logger.error(f"Failed to get klines: {resp.status}")
                    return []

    def stop(self):
        self.running = False
        logger.info("Stopping Binance WebSocket")

    def get_price_change_5m(self) -> float:
        if len(self.price_history) < 2:
            return 0.0
        
        recent = self.price_history[-1].price
        old_idx = max(0, len(self.price_history) - 20)
        old = self.price_history[old_idx].price
        
        if old == 0:
            return 0.0
        
        return (recent - old) / old
