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
from .repository import EventFilter, OddsRepository

LOGGER = logging.getLogger("parions_sport_mcp")


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
    max_markets_per_event: int = Field(default=50, ge=1, le=200)

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
    max_markets: int = Field(default=200, ge=1, le=500)

    @field_validator("market")
    @classmethod
    def clean_market(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


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
        max_markets_per_event: int = 50,
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
        self, event_id: int, market: str | None = None, max_markets: int = 200
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


def create_mcp(store: FDJOfferStore | None = None) -> FastMCP:
    tools = OddsTools(store or FDJOfferStore.from_env())
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
        max_markets_per_event: int = 50,
    ) -> dict[str, Any]:
        """Search current odds by sport, competition, event, date, or market."""

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
        max_markets: int = 200,
    ) -> dict[str, Any]:
        """Return all available odds for a specific Parions Sport event id."""

        return _run_tool(tools.get_event_odds, event_id, market, max_markets)

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
