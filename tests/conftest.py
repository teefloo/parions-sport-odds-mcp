from __future__ import annotations

import io
import sqlite3
import tempfile
import zipfile
from pathlib import Path


def create_offer_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    create_offer_schema(connection)
    seed_offer_data(connection)
    return connection


def create_offer_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table sports (
          sport_id integer,
          sport text,
          sortorder integer,
          sport_shortcut text
        );

        create table comp (
          comp_id integer,
          comp text,
          comp_order integer,
          sport_ref integer,
          comp_flag text
        );

        create table offer_1n2 (
          event_id integer,
          start_ts integer,
          fin_ts integer,
          comp_ref integer,
          foot715_ref integer,
          event_type integer,
          match text,
          foot715_type integer,
          stat_id integer,
          lotofoot_grid_numtir_interne text,
          combi_bonus_id text,
          market_count integer,
          boost_enabled integer,
          score text,
          edito text,
          betradar_id text
        );

        create table market (
          event_id integer,
          lib text,
          handicap text,
          pari_type_ref integer,
          status_ref integer,
          market_id integer,
          index_list integer,
          betTypeExclusions text,
          authorizationExclusion text,
          pos integer,
          template_id integer,
          sort_order integer,
          boost_enabled integer
        );

        create table pari_type (
          pari_type_id integer,
          pari_type text,
          pari_type_description text,
          pari_type_new integer,
          pari_type_order integer
        );

        create table buttons (
          market_id integer,
          lib text,
          cote real,
          pos integer,
          tendance integer,
          status integer,
          winner integer,
          sort_order integer,
          team text,
          button_percentage integer,
          is_hotbet integer,
          outcome_id integer
        );
        """
    )


def seed_offer_data(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "insert into sports values (?, ?, ?, ?)",
        [
            (100, "Football", 1, "Foot"),
            (600, "Tennis", 2, "Tennis"),
        ],
    )
    connection.executemany(
        "insert into comp values (?, ?, ?, ?, ?)",
        [
            (45452, "L1 McDonald's", 24, 100, "france"),
            (45550, "Rome H", 7, 600, "italie"),
        ],
    )
    connection.executemany(
        """
        insert into offer_1n2 values
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                1275790,
                1778610600,
                1778610300,
                45452,
                None,
                1,
                "Red Star-Rodez",
                None,
                3347380,
                None,
                None,
                2,
                1,
                None,
                None,
                "71411500",
            ),
            (
                1275800,
                1778697000,
                1778696700,
                45550,
                None,
                1,
                "K.Khachanov-D.Prizmic",
                None,
                3347381,
                None,
                None,
                1,
                0,
                None,
                None,
                "71411501",
            ),
        ],
    )
    connection.executemany(
        "insert into pari_type values (?, ?, ?, ?, ?)",
        [
            (1, "1/N/2", None, 0, 1),
            (7, "Plus/Moins", None, 0, 7),
        ],
    )
    connection.executemany(
        """
        insert into market values
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1275790, "1/N/2", None, 1, 1, 33122218, 9740, "", "", 2, 3, 1, 1),
            (
                1275790,
                "Plus/Moins",
                "2,5 buts (Match)",
                7,
                1,
                33122219,
                9796,
                "",
                "",
                4,
                2,
                2,
                1,
            ),
            (1275800, "Face à Face", None, 1, 1, 33122220, 9800, "", "", 1, 2, 1, 0),
        ],
    )
    connection.executemany(
        """
        insert into buttons values
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (33122218, "1", 1.82, 1, 0, 2, None, 1, "Red Star", 50, 0, 142051438),
            (33122218, "N", 3.70, 2, 0, 2, None, 2, "N", 20, 0, 142051437),
            (33122218, "2", 3.90, 3, 0, 2, None, 3, "Rodez", 30, 0, 142051436),
            (
                33122219,
                "Plus 2,5",
                1.95,
                1,
                0,
                2,
                None,
                1,
                "Plus 2,5",
                60,
                0,
                142051439,
            ),
            (
                33122219,
                "Moins 2,5",
                1.75,
                2,
                0,
                2,
                None,
                2,
                "Moins 2,5",
                40,
                0,
                142051440,
            ),
            (
                33122220,
                "K.Khachanov",
                1.52,
                1,
                0,
                2,
                None,
                1,
                "K.Khachanov",
                70,
                0,
                142051441,
            ),
            (
                33122220,
                "D.Prizmic",
                2.45,
                2,
                0,
                2,
                None,
                2,
                "D.Prizmic",
                30,
                0,
                142051442,
            ),
        ],
    )
    connection.commit()


def offer_zip_bytes() -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "spdv_mobile_offre.sqlite"
        file_connection = sqlite3.connect(db_path)
        create_offer_schema(file_connection)
        seed_offer_data(file_connection)
        file_connection.close()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_path, "spdv_mobile_offre.sqlite")
        return buffer.getvalue()
