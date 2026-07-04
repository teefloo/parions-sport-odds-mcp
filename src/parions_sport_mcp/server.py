from __future__ import annotations

import logging
import sys
from datetime import datetime, time, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import __version__
from .errors import ParionsSportError
from .fdj_client import FDJOfferStore
from .matching import extract_teams, fixture_similarity
from .repository import EventFilter, OddsRepository
from .results_client import TheSportsDBResultsClient, normalize_sport

LOGGER = logging.getLogger("parions_sport_mcp")

# Minimum fuzzy fixture score to accept an odds<->result link.
MATCH_THRESHOLD = 0.6

RESULT_DISCLAIMER = (
    "Odds are from FDJ; the linked result is from TheSportsDB and matched "
    "heuristically on team names and date. Confirm against an official source "
    "before relying on the link."
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class SearchOddsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: str | int | None = None
    competition: str | int | None = None
    event_query: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    market: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    max_markets_per_event: int | None = Field(default=None, ge=1, le=2000)

    @field_validator("event_query", "market")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def to_event_filter(self) -> EventFilter:
        return EventFilter(
            sport=self.sport,
            competition=self.competition,
            event_query=self.event_query,
            date_from=parse_tool_datetime(self.date_from, end_of_day=False),
            date_to=parse_tool_datetime(self.date_to, end_of_day=True),
            market=self.market,
            limit=self.limit,
            max_markets_per_event=self.max_markets_per_event,
        )


class EventOddsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    market: str | None = None
    max_markets: int | None = Field(default=None, ge=1, le=5000)

    @field_validator("market")
    @classmethod
    def clean_market(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class MatchResultsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sport: str | None = None
    league: str | int | None = None
    team: str | None = None
    date: str | None = None
    finished_only: bool = True
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("sport", "team")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("date")
    @classmethod
    def clean_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc
        return text


class OddsTools:
    def __init__(self, store: FDJOfferStore) -> None:
        self.store = store

    def list_sports(self) -> dict[str, Any]:
        connection, metadata, warnings = self.store.get_connection()
        try:
            sports = OddsRepository(connection).list_sports()
            return self._response({"sports": sports}, metadata, warnings)
        finally:
            connection.close()

    def list_competitions(self, sport: str | int | None = None) -> dict[str, Any]:
        connection, metadata, warnings = self.store.get_connection()
        try:
            competitions = OddsRepository(connection).list_competitions(sport)
            return self._response({"competitions": competitions}, metadata, warnings)
        finally:
            connection.close()

    def search_odds(
        self,
        sport: str | int | None = None,
        competition: str | int | None = None,
        event_query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        market: str | None = None,
        limit: int = 20,
        max_markets_per_event: int | None = None,
    ) -> dict[str, Any]:
        params = SearchOddsParams(
            sport=sport,
            competition=competition,
            event_query=event_query,
            date_from=date_from,
            date_to=date_to,
            market=market,
            limit=limit,
            max_markets_per_event=max_markets_per_event,
        )
        connection, metadata, warnings = self.store.get_connection()
        try:
            events = OddsRepository(connection).search_odds(params.to_event_filter())
            return self._response(
                {
                    "query": params.model_dump(),
                    "count": len(events),
                    "events": events,
                },
                metadata,
                warnings,
            )
        finally:
            connection.close()

    def get_event_odds(
        self, event_id: int, market: str | None = None, max_markets: int | None = None
    ) -> dict[str, Any]:
        params = EventOddsParams(
            event_id=event_id,
            market=market,
            max_markets=max_markets,
        )
        connection, metadata, warnings = self.store.get_connection()
        try:
            event = OddsRepository(connection).get_event_odds(
                params.event_id,
                market=params.market,
                max_markets=params.max_markets,
            )
            return self._response(
                {
                    "event": event,
                    "found": event is not None,
                },
                metadata,
                warnings,
            )
        finally:
            connection.close()

    @staticmethod
    def _response(
        payload: dict[str, Any], metadata: Any, warnings: list[str]
    ) -> dict[str, Any]:
        return {
            **payload,
            "source": {
                "name": "Parions Sport Point de Vente",
                "operator": "FDJ",
                "url": metadata.source_url,
                "retrieved_at": metadata.downloaded_at,
            },
            "cache": metadata.to_dict(),
            "warnings": warnings,
            "disclaimer": (
                "Odds are informational and may change. Verify official FDJ "
                "site/receipt data before relying on them."
            ),
        }


class ResultsTools:
    def __init__(self, client: TheSportsDBResultsClient) -> None:
        self.client = client

    def get_match_results(
        self,
        sport: str | None = None,
        league: str | int | None = None,
        team: str | None = None,
        date: str | None = None,
        finished_only: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        params = MatchResultsParams(
            sport=sport,
            league=league,
            team=team,
            date=date,
            finished_only=finished_only,
            limit=limit,
        )
        results, metadata = self.client.get_results(
            sport=params.sport,
            league=params.league,
            team=params.team,
            date=params.date,
            finished_only=params.finished_only,
            limit=params.limit,
        )
        return {
            "query": params.model_dump(),
            "count": len(results),
            "results": results,
            "source": {
                "name": "TheSportsDB",
                "url": metadata.source_url,
                "retrieved_at": metadata.retrieved_at,
            },
            "cache": metadata.to_dict(),
            "warnings": [],
            "disclaimer": (
                "Results are informational and provided by TheSportsDB. Verify "
                "against an official source before relying on them."
            ),
        }


class LinkResultParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int = Field(ge=1)
    day_window: int = Field(default=1, ge=0, le=3)


class LinkTools:
    def __init__(self, store: FDJOfferStore, client: TheSportsDBResultsClient) -> None:
        self.store = store
        self.client = client

    def get_event_result(self, event_id: int, day_window: int = 1) -> dict[str, Any]:
        params = LinkResultParams(event_id=event_id, day_window=day_window)

        connection, metadata, warnings = self.store.get_connection()
        try:
            event = OddsRepository(connection).get_event_odds(params.event_id)
        finally:
            connection.close()

        if event is None:
            return {
                "event_id": params.event_id,
                "found": False,
                "odds_event": None,
                "result": None,
                "match_confidence": 0.0,
                "warnings": warnings,
                "error": "Unknown Parions Sport event id.",
                "disclaimer": RESULT_DISCLAIMER,
            }

        home, away = extract_teams(event)
        sport = normalize_sport(event["sport"]["name"])
        candidates, result_source = self._collect_results(
            event["start_time"], sport, params.day_window
        )
        best, score, orientation = self._best_match(home, away, candidates)
        found = best is not None and score >= MATCH_THRESHOLD

        result_warnings = list(warnings)
        if not found:
            result_warnings.append("NO_RESULT_MATCH")

        return {
            "event_id": params.event_id,
            "found": found,
            "odds_event": {
                "event_name": event["event_name"],
                "sport": event["sport"]["name"],
                "competition": event["competition"]["name"],
                "start_time": event["start_time"],
                "teams": {"home": home, "away": away},
            },
            "result": best if found else None,
            "match_confidence": round(score, 3),
            "orientation": orientation if found else None,
            "source": {
                "odds": {
                    "name": "Parions Sport Point de Vente",
                    "operator": "FDJ",
                    "url": metadata.source_url,
                },
                "results": result_source,
            },
            "warnings": result_warnings,
            "disclaimer": RESULT_DISCLAIMER,
        }

    def _collect_results(
        self, start_time: str | None, sport: str | None, day_window: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not start_time:
            return [], None
        try:
            base = datetime.fromisoformat(start_time.replace("Z", "+00:00")).date()
        except ValueError:
            return [], None

        # Query the kickoff date first, then widen by a day each side; an event's
        # UTC calendar date can differ between providers.
        offsets = [0]
        for delta in range(1, day_window + 1):
            offsets.extend([-delta, delta])

        seen: set[Any] = set()
        candidates: list[dict[str, Any]] = []
        source: dict[str, Any] | None = None
        for offset in offsets:
            day = (base + timedelta(days=offset)).isoformat()
            results, meta = self.client.get_results(
                sport=sport, date=day, finished_only=True, limit=100
            )
            if source is None:
                source = {"name": "TheSportsDB", "url": meta.source_url}
            for result in results:
                key = result.get("match_id")
                if key not in seen:
                    seen.add(key)
                    candidates.append(result)
        return candidates, source

    @staticmethod
    def _best_match(
        home: str | None, away: str | None, candidates: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, float, str | None]:
        if not home or not away:
            return None, 0.0, None
        best: dict[str, Any] | None = None
        best_score = 0.0
        best_orientation: str | None = None
        for candidate in candidates:
            score, orientation = fixture_similarity(
                home, away, candidate.get("home_team"), candidate.get("away_team")
            )
            if score > best_score:
                best, best_score, best_orientation = candidate, score, orientation
        return best, best_score, best_orientation


def parse_tool_datetime(value: str | None, *, end_of_day: bool) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None

    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            parsed_date = datetime.strptime(text, "%Y-%m-%d").date()
            if end_of_day:
                parsed = datetime.combine(parsed_date + timedelta(days=1), time.min)
            else:
                parsed = datetime.combine(parsed_date, time.min)
            return parsed.replace(tzinfo=timezone.utc)

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"Invalid date/datetime '{value}'. Use YYYY-MM-DD or ISO 8601."
        ) from exc


def create_mcp(
    store: FDJOfferStore | None = None,
    results_client: TheSportsDBResultsClient | None = None,
) -> FastMCP:
    fdj_store = store or FDJOfferStore.from_env()
    sportsdb = results_client or TheSportsDBResultsClient.from_env()
    tools = OddsTools(fdj_store)
    results = ResultsTools(sportsdb)
    linker = LinkTools(fdj_store, sportsdb)
    mcp = FastMCP("parions-sport-odds")

    @mcp.tool()
    def list_sports() -> dict[str, Any]:
        """List sports currently available in the Parions Sport offer."""

        return _run_tool(tools.list_sports)

    @mcp.tool()
    def list_competitions(sport: str | int | None = None) -> dict[str, Any]:
        """List competitions, optionally filtered by sport name, slug, or id."""

        return _run_tool(tools.list_competitions, sport)

    @mcp.tool()
    def search_odds(
        sport: str | int | None = None,
        competition: str | int | None = None,
        event_query: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        market: str | None = None,
        limit: int = 20,
        max_markets_per_event: int | None = None,
    ) -> dict[str, Any]:
        """Search current odds by sport, competition, event, date, or market.

        Returns every market available for each matched event (1X2,
        over/under, handicap, etc.) by default. Pass max_markets_per_event to
        cap the number of markets returned per event, e.g. to limit payload
        size when scanning many events at once.
        """

        return _run_tool(
            tools.search_odds,
            sport,
            competition,
            event_query,
            date_from,
            date_to,
            market,
            limit,
            max_markets_per_event,
        )

    @mcp.tool()
    def get_event_odds(
        event_id: int,
        market: str | None = None,
        max_markets: int | None = None,
    ) -> dict[str, Any]:
        """Return all available odds for a specific Parions Sport event id.

        Every market for the event is returned by default (no truncation);
        pass max_markets only if you need to cap the response size.
        """

        return _run_tool(tools.get_event_odds, event_id, market, max_markets)

    @mcp.tool()
    def get_match_results(
        sport: str | None = None,
        league: str | int | None = None,
        team: str | None = None,
        date: str | None = None,
        finished_only: bool = True,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return finished match results (teams, score, date, competition).

        Multi-sport via TheSportsDB. Provide at least one of: date (YYYY-MM-DD),
        league (name or id), or team. sport accepts names like "football",
        "basket", "tennis", "rugby", "hockey". Set finished_only=False to also
        include scheduled/in-progress fixtures for a date.
        """

        return _run_tool(
            results.get_match_results, sport, league, team, date, finished_only, limit
        )

    @mcp.tool()
    def get_event_result(event_id: int, day_window: int = 1) -> dict[str, Any]:
        """Link a Parions Sport event to its finished result.

        Reads the event's teams and kickoff date from the FDJ offer, then fuzzy-
        matches a TheSportsDB result on team names and date. day_window widens the
        date search by N days each side (default 1). Returns the odds event, the
        matched result, and a 0..1 match_confidence; found is false below 0.6.
        """

        return _run_tool(linker.get_event_result, event_id, day_window)

    return mcp


def _run_tool(function: Any, *args: Any) -> dict[str, Any]:
    try:
        return function(*args)
    except ValidationError as exc:
        raise ValueError(f"Invalid tool input: {exc}") from exc
    except ParionsSportError as exc:
        LOGGER.error("Parions Sport tool failed: %s", exc)
        raise RuntimeError(str(exc)) from exc


def main() -> None:
    configure_logging()
    LOGGER.info("Starting parions-sport-mcp %s", __version__)
    create_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
