from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from parions_sport_mcp.fdj_client import CacheMetadata
from parions_sport_mcp.server import OddsTools, parse_tool_datetime

from .conftest import create_offer_connection


class StaticStore:
    def get_connection(self) -> tuple[sqlite3.Connection, CacheMetadata, list[str]]:
        return (
            create_offer_connection(),
            CacheMetadata(
                source_url="https://example.test/offre.zip",
                downloaded_at="2026-05-11T20:00:00Z",
                expires_at="2026-05-11T20:02:00Z",
                cache_path="/tmp/spdv_mobile_offre.sqlite",
            ),
            [],
        )


def test_tools_search_odds_shape() -> None:
    result = OddsTools(StaticStore()).search_odds(sport="football", limit=5)

    assert result["count"] == 1
    assert result["events"][0]["event_id"] == 1275790
    assert result["events"][0]["markets"][0]["outcomes"][0]["label"] == "1"
    assert result["source"]["operator"] == "FDJ"
    assert result["warnings"] == []


def test_tools_validation_rejects_invalid_limit() -> None:
    with pytest.raises(ValidationError):
        OddsTools(StaticStore()).search_odds(limit=0)


def test_parse_tool_datetime_date_to_is_exclusive_next_day() -> None:
    parsed = parse_tool_datetime("2026-05-11", end_of_day=True)

    assert parsed is not None
    assert parsed.isoformat() == "2026-05-12T00:00:00+00:00"
