from __future__ import annotations

import httpx
import pytest

from parions_sport_mcp.errors import InputValidationError, RateLimitedError
from parions_sport_mcp.results_client import TheSportsDBResultsClient
from parions_sport_mcp.server import ResultsTools

SOCCER_DAY = {
    "events": [
        {
            "idEvent": "2052305",
            "strSport": "Soccer",
            "strLeague": "French Ligue 1",
            "idLeague": "4334",
            "strSeason": "2025-2026",
            "intRound": "33",
            "dateEvent": "2026-05-12",
            "strTimestamp": "2026-05-12T18:30:00",
            "strStatus": "Match Finished",
            "strHomeTeam": "Red Star",
            "strAwayTeam": "Rodez",
            "intHomeScore": "2",
            "intAwayScore": "1",
        },
        {
            "idEvent": "2052306",
            "strSport": "Soccer",
            "strLeague": "Serie A",
            "idLeague": "4332",
            "strHomeTeam": "Roma",
            "strAwayTeam": "Lazio",
            "strStatus": "Not Started",
            "intHomeScore": None,
            "intAwayScore": None,
        },
    ]
}


def _client(handler) -> TheSportsDBResultsClient:
    return TheSportsDBResultsClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_get_results_by_date_filters_to_finished() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["d"] == "2026-05-12"
        assert request.url.params["s"] == "Soccer"
        return httpx.Response(200, json=SOCCER_DAY)

    results, metadata = _client(handler).get_results(sport="football", date="2026-05-12")

    assert metadata.cached is False
    assert len(results) == 1
    assert results[0]["home_team"] == "Red Star"
    assert results[0]["score"] == {"home": 2, "away": 1}


def test_get_results_caches_repeated_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=SOCCER_DAY)

    client = _client(handler)
    client.get_results(sport="football", date="2026-05-12")
    _, metadata = client.get_results(sport="football", date="2026-05-12")

    assert calls == 1
    assert metadata.cached is True


def test_finished_only_excludes_future_dated_events() -> None:
    future_day = {
        "events": [
            {
                "idEvent": "1",
                "strSport": "Soccer",
                "strHomeTeam": "PSG",
                "strAwayTeam": "Lyon",
                "strTimestamp": "2999-01-01T20:00:00",
                "strStatus": "",
                "intHomeScore": "0",  # placeholder score on a not-yet-played match
                "intAwayScore": "0",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=future_day)

    results, _ = _client(handler).get_results(sport="football", date="2999-01-01")

    assert results == []


def test_get_results_requires_a_filter() -> None:
    with pytest.raises(InputValidationError):
        _client(lambda request: httpx.Response(200, json={})).get_results()


def test_get_results_by_league_name_uses_alias_without_listing_call() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"events": SOCCER_DAY["events"][:1]})

    results, _ = _client(handler).get_results(sport="football", league="Ligue 1")

    # Resolved via LEAGUE_ALIASES -> id 4334, no all_leagues.php round-trip.
    assert paths == ["/api/v1/json/3/eventspastleague.php"]
    assert results[0]["home_team"] == "Red Star"


def test_rate_limit_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(RateLimitedError):
        _client(handler).get_results(date="2026-05-12")


def test_tools_response_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SOCCER_DAY)

    result = ResultsTools(_client(handler)).get_match_results(
        sport="football", date="2026-05-12"
    )

    assert result["count"] == 1
    assert result["source"]["name"] == "TheSportsDB"
    assert result["results"][0]["league"] == "French Ligue 1"
    assert result["warnings"] == []
