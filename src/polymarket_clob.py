import asyncio
import aiohttp
import logging
import time
import ssl
import os
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
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(ssl=ssl_context)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            connector=connector,
            timeout=timeout,
        )
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
                    if isinstance(data, dict) and 'data' in data:
                        return data['data']
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

    async def find_sports_markets(self, sport: str = None) -> List[Dict]:
        """Find live sports moneyline markets on Polymarket."""
        url = f"{CLOB_BASE_URL}/markets"
        params = {"active": "true", "closed": "false"}
        if sport:
            params["tag"] = sport

        try:
            async with self.session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to get sports markets: {resp.status}")
                    return []
                data = await resp.json()
                markets = data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.error(f"Error getting sports markets: {e}")
            return []

        results = []
        for m in markets:
            question = m.get("question", "").lower()
            market_type = m.get("sportsMarketType", "")
            if market_type and market_type != "moneyline":
                continue

            sport_keywords = {
                "nba": ["nba", "basketball"],
                "nhl": ["nhl", "hockey"],
                "mlb": ["mlb", "baseball"],
            }

            is_sports = False
            matched_sport = None
            for s, keywords in sport_keywords.items():
                if any(kw in question for kw in keywords):
                    is_sports = True
                    matched_sport = s
                    break

            if not is_sports:
                continue

            if m.get("closed") or m.get("archived"):
                continue
            if not m.get("accepting_orders", False):
                continue
            if not m.get("condition_id"):
                continue

            if sport and matched_sport != sport:
                continue

            tokens_raw = m.get("tokens", [])
            outcomes = m.get("outcomes", [])

            market_info = {
                "condition_id": m.get("condition_id", ""),
                "question": m.get("question", ""),
                "sport": matched_sport,
                "home_token": None,
                "away_token": None,
                "market_id": m.get("id", ""),
                "active": m.get("active", False),
                "closed": m.get("closed", False),
                "live": m.get("live", False),
                "score": m.get("score", {}),
                "seconds_delay": m.get("secondsDelay", 0),
                "end_date_iso": m.get("endDateIso", ""),
            }

            if outcomes and isinstance(outcomes, list) and len(outcomes) >= 2:
                market_info["home_token"] = outcomes[0].get("token_id", "")
                market_info["away_token"] = outcomes[1].get("token_id", "")
            elif tokens_raw and isinstance(tokens_raw, list) and len(tokens_raw) >= 2:
                market_info["home_token"] = tokens_raw[0].get("token_id", "")
                market_info["away_token"] = tokens_raw[1].get("token_id", "")

            if market_info["home_token"] and market_info["away_token"]:
                results.append(market_info)

        logger.info(f"Found {len(results)} sports markets" + (f" for {sport}" if sport else ""))
        return results

    async def get_event_markets(self, event_id: str) -> List[Dict]:
        """Get all markets for a specific Polymarket event."""
        url = f"{CLOB_BASE_URL}/events/{event_id}"

        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                markets = data.get("markets", [])
                return markets
        except Exception as e:
            logger.error(f"Error getting event markets: {e}")
            return []

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
                    logger.debug(f"Failed to get order book: {resp.status}")
                    return None
        except Exception as e:
            logger.debug(f"Error getting order book: {e}")
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
