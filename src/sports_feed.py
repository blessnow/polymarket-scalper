import asyncio
import logging
from typing import Callable, Optional, Dict, List
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
import ssl

logger = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

SPORTS = {
    "nba": {"path": "basketball/nba", "periods": 4, "period_seconds": 720},
    "nhl": {"path": "ice-hockey/nhl", "periods": 3, "period_seconds": 1200},
    "mlb": {"path": "baseball/mlb", "periods": 9, "period_seconds": 0},
}


@dataclass
class ScoreEvent:
    game_id: str
    sport: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    scoring_team: str  # "home" or "away"
    timestamp: datetime
    game_state: str  # "live", "final", "pre"


@dataclass
class LiveGame:
    game_id: str
    sport: str
    home_team: str
    away_team: str
    home_score: int = 0
    away_score: int = 0
    period: int = 0
    time_remaining: str = ""
    state: str = "pre"
    last_update: datetime = field(default_factory=datetime.now)

    @property
    def score_diff(self) -> int:
        return self.home_score - self.away_score


class SportsFeed:
    def __init__(
        self,
        leagues: List[str] = None,
        poll_interval: float = 5.0,
        on_score_change: Optional[Callable] = None,
    ):
        self.leagues = leagues or ["nba", "nhl", "mlb"]
        self.poll_interval = poll_interval
        self.on_score_change = on_score_change
        self.running = False
        self.session: Optional[aiohttp.ClientSession] = None
        self.live_games: Dict[str, LiveGame] = {}
        self.score_history: List[ScoreEvent] = []
        self.max_history = 500

    async def connect(self):
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        self.running = True
        logger.info(f"SportsFeed connected, monitoring: {self.leagues}")
        await self._poll_loop()

    async def _poll_loop(self):
        while self.running:
            for league in self.leagues:
                try:
                    await self._fetch_scoreboard(league)
                except Exception as e:
                    logger.error(f"Error fetching {league} scoreboard: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _fetch_scoreboard(self, league: str):
        if league not in SPORTS:
            return
        path = SPORTS[league]["path"]
        url = f"{ESPN_BASE}/{path}/scoreboard"

        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"ESPN {league} returned {resp.status}")
                    return
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"ESPN {league} request failed: {e}")
            return

        events = data.get("events", [])
        for event in events:
            self._process_event(event, league)

    def _process_event(self, event: dict, league: str):
        event_id = event.get("id", "")
        status = event.get("status", {})
        type_info = status.get("type", {})
        state = type_info.get("state", "pre").lower()
        period = type_info.get("period", 0)
        time_remaining = status.get("displayClock", "")

        competitions = event.get("competitions", [])
        if not competitions:
            return
        comp = competitions[0]
        competitors = comp.get("competitors", [])

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)

        if not home or not away:
            return

        home_team = home.get("team", {}).get("abbreviation", "???")
        away_team = away.get("team", {}).get("abbreviation", "???")
        home_score = int(home.get("score", 0))
        away_score = int(away.get("score", 0))

        game_key = f"{league}:{event_id}"
        prev = self.live_games.get(game_key)

        game = LiveGame(
            game_id=event_id,
            sport=league,
            home_team=home_team,
            away_team=away_team,
            home_score=home_score,
            away_score=away_score,
            period=period,
            time_remaining=time_remaining,
            state=state,
            last_update=datetime.now(),
        )

        if state not in ("in", "live"):
            if prev:
                del self.live_games[game_key]
            return

        if prev is None:
            self.live_games[game_key] = game
            logger.info(f"Live game: {away_team} @ {home_team} ({home_score}-{away_score}) Q{period}")
            return

        if home_score != prev.home_score or away_score != prev.away_score:
            scoring_team = "home"
            if away_score != prev.away_score and home_score == prev.home_score:
                scoring_team = "away"

            evt = ScoreEvent(
                game_id=event_id,
                sport=league,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                period=period,
                time_remaining=time_remaining,
                scoring_team=scoring_team,
                timestamp=datetime.now(),
                game_state=state,
            )

            self.score_history.append(evt)
            if len(self.score_history) > self.max_history:
                self.score_history.pop(0)

            self.live_games[game_key] = game

            logger.info(
                f"SCORE: {scoring_team.upper()} scores in {away_team}@{home_team} "
                f"{home_score}-{away_score} Q{period} {time_remaining}"
            )

            if self.on_score_change:
                asyncio.create_task(self.on_score_change(evt))

        self.live_games[game_key] = game

    def get_live_games(self) -> List[LiveGame]:
        return list(self.live_games.values())

    def get_game(self, game_id: str) -> Optional[LiveGame]:
        for key, game in self.live_games.items():
            if game.game_id == game_id:
                return game
        return None

    def stop(self):
        self.running = False
        logger.info("Stopping SportsFeed")

    async def close(self):
        self.stop()
        if self.session:
            await self.session.close()
