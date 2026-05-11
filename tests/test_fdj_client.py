from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from parions_sport_mcp.errors import InvalidSourceDataError
from parions_sport_mcp.fdj_client import FDJOfferStore
from parions_sport_mcp.repository import OddsRepository

from .conftest import offer_zip_bytes


def test_store_downloads_validates_and_opens_in_memory_database(tmp_path) -> None:
    payload = offer_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"cache-control": "max-age=120", "etag": '"abc"'},
            content=payload,
        )

    store = FDJOfferStore(
        source_url="https://example.test/offre.zip",
        cache_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    connection, metadata, warnings = store.get_connection()
    try:
        sports = OddsRepository(connection).list_sports()
    finally:
        connection.close()

    assert warnings == []
    assert metadata.etag == '"abc"'
    assert metadata.stale is False
    assert sports[0]["name"] == "Football"
    assert (tmp_path / "spdv_mobile_offre.sqlite").exists()


def test_store_uses_stale_cache_when_refresh_fails(tmp_path) -> None:
    payload = offer_zip_bytes()
    now = {"value": datetime(2026, 5, 11, 20, 0, tzinfo=timezone.utc)}
    calls = 0

    def current_time() -> datetime:
        return now["value"]

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, headers={"cache-control": "max-age=120"}, content=payload)
        raise httpx.ConnectError("offline", request=request)

    store = FDJOfferStore(
        source_url="https://example.test/offre.zip",
        cache_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now_func=current_time,
    )

    first_connection, _, first_warnings = store.get_connection()
    first_connection.close()
    now["value"] = now["value"] + timedelta(seconds=130)
    second_connection, metadata, second_warnings = store.get_connection()
    second_connection.close()

    assert first_warnings == []
    assert "STALE_CACHE_USED" in second_warnings
    assert metadata.stale is True


def test_store_rejects_malformed_zip_without_cache(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not a zip")

    store = FDJOfferStore(
        source_url="https://example.test/offre.zip",
        cache_dir=tmp_path,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(InvalidSourceDataError):
        store.get_connection()
