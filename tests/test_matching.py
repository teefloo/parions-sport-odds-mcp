from __future__ import annotations

import sqlite3

from parions_sport_mcp.fdj_client import CacheMetadata
from parions_sport_mcp.matching import (
    extract_teams,
    fixture_similarity,
    team_similarity,
)
from parions_sport_mcp.results_client import FetchMetadata
from parions_sport_mcp.server import LinkTools

from .conftest import create_offer_connection


def test_team_similarity_tolerates_suffixes_and_accents() -> None:
    assert team_similarity("Red Star", "Red Star FC") > 0.9
    assert team_similarity("St Etienne", "Saint-Étienne") > 0.5
    assert team_similarity("Red Star", "Rodez") < 0.5


def test_fixture_similarity_detects_swapped_orientation() -> None:
    score, orientation = fixture_similarity(
        "Red Star", "Rodez", "Rodez AF", "Red Star FC"
    )
    assert orientation == "swapped"
    assert score > 0.9


def test_extract_teams_prefers_head_to_head_market() -> None:
    event = {
        "event_name": "Red Star-Rodez",
        "markets": [
            {
                "outcomes": [
                    {"label": "1", "team": "Red Star"},
                    {"label": "N", "team": "N"},
                    {"label": "2", "team": "Rodez"},
                ]
            }
        ],
    }
    assert extract_teams(event) == ("Red Star", "Rodez")


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


class FakeResultsClient:
    """Returns a canned Red Star vs Rodez result regardless of the date."""

    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events if events is not None else [
            {
                "match_id": "2052305",
                "sport": "Soccer",
                "league": "French Ligue 1",
                "date": "2026-05-12T18:30:00",
                "status": "Match Finished",
                "home_team": "Red Star FC",
                "away_team": "Rodez AF",
                "score": {"home": 2, "away": 1},
            }
        ]
        self.calls: list[str] = []

    def get_results(self, sport=None, date=None, finished_only=True, limit=20):
        self.calls.append(date)
        return list(self.events), FetchMetadata(
            source_url="https://thesportsdb.test", retrieved_at="2026-06-20T00:00:00Z"
        )


def test_get_event_result_links_fdj_event_to_result() -> None:
    linker = LinkTools(StaticStore(), FakeResultsClient())

    result = linker.get_event_result(event_id=1275790, day_window=1)

    assert result["found"] is True
    assert result["odds_event"]["teams"] == {"home": "Red Star", "away": "Rodez"}
    assert result["result"]["score"] == {"home": 2, "away": 1}
    assert result["match_confidence"] > 0.9
    assert result["warnings"] == []


def test_get_event_result_dedups_results_across_day_window() -> None:
    client = FakeResultsClient()
    LinkTools(StaticStore(), client).get_event_result(event_id=1275790, day_window=1)

    # 3 days queried (kickoff +/- 1) but the same match id is collected once.
    assert len(client.calls) == 3


def test_get_event_result_reports_no_match_below_threshold() -> None:
    client = FakeResultsClient(
        events=[
            {
                "match_id": "999",
                "home_team": "Paris SG",
                "away_team": "Marseille",
                "score": {"home": 0, "away": 0},
                "date": "2026-05-12T18:30:00",
            }
        ]
    )
    result = LinkTools(StaticStore(), client).get_event_result(event_id=1275790)

    assert result["found"] is False
    assert result["result"] is None
    assert "NO_RESULT_MATCH" in result["warnings"]


def test_get_event_result_unknown_event_id() -> None:
    result = LinkTools(StaticStore(), FakeResultsClient()).get_event_result(event_id=42)

    assert result["found"] is False
    assert result["error"] == "Unknown Parions Sport event id."
