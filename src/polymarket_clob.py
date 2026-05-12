import asyncio
import aiohttp
import logging
import time
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

CLOB_BASE_URL = "https://clob.polymarket.com"


@dataclass
class Market:
    condition_id: str
    token_a: str
    token_b: str
    question: str
    description: str
    active: bool


@dataclass
class OrderBook:
    market: str
    bids: List[Dict]
    asks: List[Dict]
    timestamp: datetime


class PolymarketCLOB:
    def __init__(self, api_key: str = None, api_secret: str = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session: Optional[aiohttp.ClientSession] = None
        self.headers = {"Content-Type": "application/json"}
        
        if api_key:
            self.headers["API-Key"] = api_key

    async def connect(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        logger.info("Connected to Polymarket CLOB")

    async def close(self):
        if self.session:
            await self.session.close()

    async def get_markets(self) -> List[Dict]:
        url = f"{CLOB_BASE_URL}/markets"
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    logger.error(f"Failed to get markets: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error getting markets: {e}")
            return []

    async def find_btc_5min_market(self) -> Optional[Market]:
        markets = await self.get_markets()
        
        for m in markets:
            question = m.get("question", "").lower()
            if "btc" in question or "bitcoin" in question:
                if "5 min" in question or "5min" in question or "up/down" in question:
                    return Market(
                        condition_id=m.get("condition_id", ""),
                        token_a=m.get("tokens", {}).get("token_a", ""),
                        token_b=m.get("tokens", {}).get("token_b", ""),
                        question=m.get("question", ""),
                        description=m.get("description", ""),
                        active=m.get("active", False)
                    )
        
        logger.warning("BTC 5MIN market not found")
        return None

    async def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        url = f"{CLOB_BASE_URL}/book"
        params = {"token_id": token_id}
        
        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return OrderBook(
                        market=token_id,
                        bids=data.get("bids", []),
                        asks=data.get("asks", []),
                        timestamp=datetime.now()
                    )
                else:
                    logger.error(f"Failed to get order book: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"Error getting order book: {e}")
            return None

    async def get_price(self, token_id: str) -> Optional[float]:
        order_book = await self.get_order_book(token_id)
        
        if not order_book:
            return None
        
        if order_book.asks:
            best_ask = float(order_book.asks[0].get("price", 0))
            if best_ask > 0:
                return best_ask
        
        if order_book.bids:
            best_bid = float(order_book.bids[0].get("price", 0))
            if best_bid > 0:
                return best_bid
        
        return None

    async def get_mid_price(self, token_id: str) -> Optional[float]:
        order_book = await self.get_order_book(token_id)
        
        if not order_book:
            return None
        
        best_bid = 0.0
        best_ask = 0.0
        
        if order_book.bids:
            best_bid = float(order_book.bids[0].get("price", 0))
        
        if order_book.asks:
            best_ask = float(order_book.asks[0].get("price", 0))
        
        if best_bid > 0 and best_ask > 0:
            return (best_bid + best_ask) / 2
        
        return None

    async def place_order(self, token_id: str, side: str, price: float, size: float) -> Optional[Dict]:
        if not self.api_key:
            logger.warning("No API key configured, cannot place order")
            return None
        
        url = f"{CLOB_BASE_URL}/order"
        
        payload = {
            "token_id": token_id,
            "side": side,
            "price": str(price),
            "size": str(size),
            "expiration": int(time.time()) + 300
        }
        
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(f"Order placed: {side} {size} @ {price}")
                    return data
                else:
                    text = await resp.text()
                    logger.error(f"Failed to place order: {resp.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        if not self.api_key:
            return False
        
        url = f"{CLOB_BASE_URL}/order/{order_id}"
        
        try:
            async with self.session.delete(url) as resp:
                if resp.status == 200:
                    logger.info(f"Order cancelled: {order_id}")
                    return True
                else:
                    logger.error(f"Failed to cancel order: {resp.status}")
                    return False
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    async def get_positions(self) -> List[Dict]:
        if not self.api_key:
            return []
        
        url = f"{CLOB_BASE_URL}/positions"
        
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                else:
                    logger.error(f"Failed to get positions: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return []
