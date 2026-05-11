import os

import pytest

from parions_sport_mcp.fdj_client import FDJOfferStore
from parions_sport_mcp.repository import OddsRepository


@pytest.mark.skipif(
    os.getenv("FDJ_LIVE_TESTS") != "1",
    reason="Set FDJ_LIVE_TESTS=1 to download the live official FDJ offer ZIP.",
)
def test_live_fdj_zip_has_expected_schema(tmp_path) -> None:
    store = FDJOfferStore(cache_dir=tmp_path)
    connection, metadata, warnings = store.get_connection()
    try:
        OddsRepository.assert_schema(connection)
        sports = OddsRepository(connection).list_sports()
    finally:
        connection.close()

    assert sports
    assert metadata.source_url.startswith("https://www.pointdevente.parionssport.fdj.fr/")
    assert warnings == []
