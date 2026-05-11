from datetime import datetime, timezone

from parions_sport_mcp.repository import EventFilter, OddsRepository

from .conftest import create_offer_connection


def test_list_sports_returns_available_sports() -> None:
    connection = create_offer_connection()
    try:
        sports = OddsRepository(connection).list_sports()
    finally:
        connection.close()

    assert [sport["slug"] for sport in sports] == ["football", "tennis"]


def test_search_odds_filters_by_sport_competition_and_event() -> None:
    connection = create_offer_connection()
    try:
        events = OddsRepository(connection).search_odds(
            EventFilter(
                sport="football",
                competition="l1-mcdonald-s",
                event_query="rode",
                limit=10,
            )
        )
    finally:
        connection.close()

    assert len(events) == 1
    assert events[0]["event_name"] == "Red Star-Rodez"
    assert events[0]["sport"]["name"] == "Football"
    assert events[0]["returned_market_count"] == 2
    assert events[0]["markets"][0]["outcomes"][0]["odds"] == 1.82


def test_search_odds_filters_market_and_date() -> None:
    connection = create_offer_connection()
    try:
        events = OddsRepository(connection).search_odds(
            EventFilter(
                sport=100,
                market="plus",
                date_from=datetime(2026, 5, 12, tzinfo=timezone.utc),
                date_to=datetime(2026, 5, 13, tzinfo=timezone.utc),
                limit=10,
            )
        )
    finally:
        connection.close()

    assert len(events) == 1
    assert events[0]["returned_market_count"] == 1
    assert events[0]["markets"][0]["market_name"] == "Plus/Moins"


def test_get_event_odds_returns_none_for_missing_event() -> None:
    connection = create_offer_connection()
    try:
        event = OddsRepository(connection).get_event_odds(999999)
    finally:
        connection.close()

    assert event is None
