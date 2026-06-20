from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .errors import InputValidationError, RateLimitedError, SourceUnavailableError

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.thesportsdb.com/api/v1/json"
# "3" is TheSportsDB's free public test key. Set THESPORTSDB_API_KEY to a
# personal/Patreon key for higher limits and fresher data.
DEFAULT_API_KEY = "3"
DEFAULT_TTL_SECONDS = 300

# Map common French/English sport names to TheSportsDB canonical labels.
SPORT_ALIASES = {
    "football": "Soccer",
    "foot": "Soccer",
    "soccer": "Soccer",
    "basket": "Basketball",
    "basketball": "Basketball",
    "tennis": "Tennis",
    "rugby": "Rugby",
    "hockey": "Ice Hockey",
    "ice-hockey": "Ice Hockey",
    "handball": "Handball",
    "baseball": "Baseball",
    "volleyball": "Volleyball",
    "american-football": "American Football",
    "nfl": "American Football",
    "motorsport": "Motorsport",
    "f1": "Motorsport",
    "formula-1": "Motorsport",
    "golf": "Golf",
    "cricket": "Cricket",
    "darts": "Darts",
    "mma": "Fighting",
}

# Curated TheSportsDB league ids for popular competitions. The free tier caps
# the league-listing endpoints at a handful of rows, so name lookups can't rely
# on the API alone; this map resolves the common cases offline.
LEAGUE_ALIASES = {
    "ligue 1": "4334",
    "ligue1": "4334",
    "french ligue 1": "4334",
    "ligue 2": "4401",
    "premier league": "4328",
    "english premier league": "4328",
    "epl": "4328",
    "championship": "4329",
    "la liga": "4335",
    "laliga": "4335",
    "primera division": "4335",
    "serie a": "4332",
    "bundesliga": "4331",
    "eredivisie": "4337",
    "primeira liga": "4344",
    "champions league": "4480",
    "uefa champions league": "4480",
    "ldc": "4480",
    "nba": "4387",
    "nfl": "4391",
    "nhl": "4380",
    "mlb": "4424",
}

# Statuses TheSportsDB uses to mark a completed event.
FINISHED_STATUSES = {"match finished", "ft", "aet", "ap", "finished", "fin"}


@dataclass
class FetchMetadata:
    source_url: str
    retrieved_at: str
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"retrieved_at": self.retrieved_at, "cached": self.cached}


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_sport(value: str | None) -> str | None:
    if not value:
        return None
    return SPORT_ALIASES.get(value.lower().strip(), value.strip().title())


class TheSportsDBResultsClient:
    """Fetch finished match results from TheSportsDB (multi-sport, free tier)."""

    def __init__(
        self,
        api_key: str = DEFAULT_API_KEY,
        base_url: str = DEFAULT_BASE_URL,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = 20.0,
        http_client: httpx.Client | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.now_func = now_func or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @classmethod
    def from_env(cls) -> TheSportsDBResultsClient:
        return cls(
            api_key=os.getenv("THESPORTSDB_API_KEY", DEFAULT_API_KEY),
            base_url=os.getenv("THESPORTSDB_BASE_URL", DEFAULT_BASE_URL),
            ttl_seconds=int(os.getenv("THESPORTSDB_CACHE_TTL_SECONDS", "300")),
            timeout_seconds=float(os.getenv("THESPORTSDB_TIMEOUT_SECONDS", "20")),
        )

    def get_results(
        self,
        sport: str | None = None,
        league: str | int | None = None,
        team: str | None = None,
        date: str | None = None,
        finished_only: bool = True,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], FetchMetadata]:
        canonical_sport = normalize_sport(sport)

        if date:
            params = {"d": date}
            if canonical_sport:
                params["s"] = canonical_sport
            events, metadata = self._fetch("eventsday.php", params)
            if league:
                events = self._filter_by_league(events, league)
            if team:
                events = self._filter_by_team(events, team)
        elif league is not None:
            league_id = self._resolve_league_id(league, canonical_sport)
            events, metadata = self._fetch("eventspastleague.php", {"id": league_id})
        elif team:
            team_id = self._resolve_team_id(team, canonical_sport)
            events, metadata = self._fetch("eventslast.php", {"id": team_id})
            events = [event.get("event", event) for event in events]
        else:
            raise InputValidationError(
                "Provide at least one of: date (YYYY-MM-DD), league, or team."
            )

        results = [self._normalize(event) for event in events]
        if finished_only:
            results = [r for r in results if self._is_finished(r)]
        return results[:limit], metadata

    def _fetch(
        self, path: str, params: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], FetchMetadata]:
        payload, metadata = self._request(path, params)
        # TheSportsDB returns {"events": null} (and sometimes {"results": ...})
        # when nothing matches.
        events = payload.get("events")
        if events is None:
            events = payload.get("results")
        return list(events or []), metadata

    def _request(
        self, path: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any], FetchMetadata]:
        url = f"{self.base_url}/{self.api_key}/{path}"
        cache_key = f"{url}?{sorted(params.items())}"

        with self._lock:
            now = time.monotonic()
            cached = self._cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1], FetchMetadata(
                    source_url=url, retrieved_at=self._now_iso(), cached=True
                )

            client = self.http_client or httpx.Client(timeout=self.timeout_seconds)
            close_client = self.http_client is None
            try:
                response = client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise SourceUnavailableError(
                    f"Could not reach TheSportsDB: {exc}"
                ) from exc
            finally:
                if close_client:
                    client.close()

            if response.status_code == 429:
                raise RateLimitedError(
                    "TheSportsDB rate limit reached; retry later or use a personal key."
                )
            if response.status_code >= 400:
                raise SourceUnavailableError(
                    f"TheSportsDB returned HTTP {response.status_code}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise SourceUnavailableError(
                    "TheSportsDB returned a non-JSON response"
                ) from exc

            self._cache[cache_key] = (now + self.ttl_seconds, payload)
            return payload, FetchMetadata(
                source_url=url, retrieved_at=self._now_iso(), cached=False
            )

    def _resolve_league_id(self, league: str | int, sport: str | None) -> str:
        if isinstance(league, int) or str(league).isdigit():
            return str(league)

        target = str(league).lower().strip()
        if target in LEAGUE_ALIASES:
            return LEAGUE_ALIASES[target]

        # Fall back to the API listing (capped on the free tier, best effort).
        payload, _ = self._request("all_leagues.php", {})
        for entry in payload.get("leagues") or []:
            name = (entry.get("strLeague") or "").lower()
            if target in name and (
                sport is None or (entry.get("strSport") or "") == sport
            ):
                return str(entry.get("idLeague"))
        raise InputValidationError(
            f"No league matched '{league}'. Pass a numeric TheSportsDB league id "
            "or one of the known names (e.g. 'Ligue 1', 'Premier League', 'NBA')."
        )

    def _resolve_team_id(self, team: str, sport: str | None) -> str:
        payload, _ = self._request("searchteams.php", {"t": team})
        teams = payload.get("teams") or []
        for entry in teams:
            if sport is None or (entry.get("strSport") or "") == sport:
                return str(entry.get("idTeam"))
        raise InputValidationError(f"No team matched '{team}'.")

    @staticmethod
    def _filter_by_league(
        events: list[dict[str, Any]], league: str | int
    ) -> list[dict[str, Any]]:
        target = str(league).lower().strip()
        return [
            event
            for event in events
            if target == str(event.get("idLeague"))
            or target in (event.get("strLeague") or "").lower()
        ]

    @staticmethod
    def _filter_by_team(
        events: list[dict[str, Any]], team: str
    ) -> list[dict[str, Any]]:
        target = team.lower().strip()
        return [
            event
            for event in events
            if target in (event.get("strHomeTeam") or "").lower()
            or target in (event.get("strAwayTeam") or "").lower()
        ]

    def _is_finished(self, result: dict[str, Any]) -> bool:
        # A match scheduled in the future is never a result, even if the feed
        # carries a placeholder 0-0 score.
        when = self._parse_event_dt(result.get("date"))
        if when is not None and when > self.now_func():
            return False
        status = (result.get("status") or "").lower()
        if status in FINISHED_STATUSES:
            return True
        # TheSportsDB often leaves strStatus empty even for played games; treat a
        # present home score as evidence the match is over.
        return result["score"]["home"] is not None

    @staticmethod
    def _parse_event_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        text = value.strip().replace("Z", "+00:00")
        for parser in (datetime.fromisoformat, lambda v: datetime.strptime(v, "%Y-%m-%d")):
            try:
                parsed = parser(text)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return None

    @staticmethod
    def _normalize(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_id": event.get("idEvent"),
            "sport": event.get("strSport"),
            "league": event.get("strLeague"),
            "league_id": event.get("idLeague"),
            "season": event.get("strSeason"),
            "round": event.get("intRound"),
            "date": event.get("strTimestamp") or event.get("dateEvent"),
            "status": event.get("strStatus"),
            "home_team": event.get("strHomeTeam"),
            "away_team": event.get("strAwayTeam"),
            "score": {
                "home": _to_int(event.get("intHomeScore")),
                "away": _to_int(event.get("intAwayScore")),
            },
        }

    def _now_iso(self) -> str:
        return self.now_func().isoformat().replace("+00:00", "Z")
