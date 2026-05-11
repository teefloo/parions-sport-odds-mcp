from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .errors import SchemaDriftError


REQUIRED_SCHEMA: dict[str, set[str]] = {
    "offer_1n2": {
        "event_id",
        "start_ts",
        "fin_ts",
        "comp_ref",
        "match",
        "market_count",
        "boost_enabled",
        "score",
        "betradar_id",
    },
    "comp": {"comp_id", "comp", "comp_order", "sport_ref", "comp_flag"},
    "sports": {"sport_id", "sport", "sortorder", "sport_shortcut"},
    "market": {
        "event_id",
        "lib",
        "handicap",
        "pari_type_ref",
        "status_ref",
        "market_id",
        "index_list",
        "pos",
        "sort_order",
        "boost_enabled",
    },
    "pari_type": {"pari_type_id", "pari_type"},
    "buttons": {
        "market_id",
        "lib",
        "cote",
        "pos",
        "tendance",
        "status",
        "sort_order",
        "team",
        "button_percentage",
        "is_hotbet",
        "outcome_id",
    },
}


def slugify(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFD", value)
    ascii_value = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def ts_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


@dataclass(frozen=True)
class EventFilter:
    sport: str | int | None = None
    competition: str | int | None = None
    event_query: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    market: str | None = None
    limit: int = 20
    max_markets_per_event: int = 50


class OddsRepository:
    """Read-only access to the FDJ Parions Sport offer database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.connection.row_factory = sqlite3.Row

    @staticmethod
    def assert_schema(connection: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }
        missing_tables = sorted(set(REQUIRED_SCHEMA) - tables)
        if missing_tables:
            raise SchemaDriftError(
                "FDJ offer database is missing expected tables: "
                + ", ".join(missing_tables)
            )

        missing_columns: list[str] = []
        for table, expected_columns in REQUIRED_SCHEMA.items():
            columns = {
                row[1] for row in connection.execute(f"pragma table_info({table})")
            }
            missing = sorted(expected_columns - columns)
            if missing:
                missing_columns.append(f"{table}: {', '.join(missing)}")

        if missing_columns:
            raise SchemaDriftError(
                "FDJ offer database schema has changed; missing columns: "
                + "; ".join(missing_columns)
            )

    def list_sports(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            select sport_id, sport, sport_shortcut, sortorder
            from sports
            where sport_id in (
              select distinct c.sport_ref
              from comp c
              join offer_1n2 e on e.comp_ref = c.comp_id
            )
            order by sortorder, sport collate nocase
            """
        ).fetchall()
        return [
            {
                "sport_id": row["sport_id"],
                "name": row["sport"],
                "slug": slugify(row["sport"]),
                "shortcut": row["sport_shortcut"],
                "sort_order": row["sortorder"],
            }
            for row in rows
        ]

    def list_competitions(self, sport: str | int | None = None) -> list[dict[str, Any]]:
        sport_id = self._resolve_sport_id(sport) if sport is not None else None
        params: list[Any] = []
        where = [
            "exists (select 1 from offer_1n2 e where e.comp_ref = c.comp_id)",
        ]
        if sport_id is not None:
            where.append("c.sport_ref = ?")
            params.append(sport_id)

        rows = self.connection.execute(
            f"""
            select c.comp_id, c.comp, c.comp_order, c.comp_flag,
                   s.sport_id, s.sport, s.sport_shortcut
            from comp c
            join sports s on s.sport_id = c.sport_ref
            where {" and ".join(where)}
            order by s.sortorder, c.comp_order, c.comp collate nocase
            """,
            params,
        ).fetchall()
        return [
            {
                "competition_id": row["comp_id"],
                "name": row["comp"],
                "slug": slugify(row["comp"]),
                "flag": row["comp_flag"],
                "sort_order": row["comp_order"],
                "sport": {
                    "sport_id": row["sport_id"],
                    "name": row["sport"],
                    "slug": slugify(row["sport"]),
                    "shortcut": row["sport_shortcut"],
                },
            }
            for row in rows
        ]

    def search_odds(self, filters: EventFilter) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []

        sport_id = self._resolve_sport_id(filters.sport) if filters.sport is not None else None
        competition_id = (
            self._resolve_competition_id(filters.competition, sport_id)
            if filters.competition is not None
            else None
        )

        if sport_id is not None:
            clauses.append("s.sport_id = ?")
            params.append(sport_id)
        if competition_id is not None:
            clauses.append("c.comp_id = ?")
            params.append(competition_id)
        if filters.event_query:
            clauses.append("lower(e.match) like ?")
            params.append(f"%{filters.event_query.lower()}%")
        if filters.date_from:
            clauses.append("e.start_ts >= ?")
            params.append(int(filters.date_from.timestamp()))
        if filters.date_to:
            clauses.append("e.start_ts < ?")
            params.append(int(filters.date_to.timestamp()))
        if filters.market:
            clauses.append(
                """
                exists (
                  select 1
                  from market m
                  left join pari_type pt on pt.pari_type_id = m.pari_type_ref
                  where m.event_id = e.event_id
                    and (
                      lower(m.lib) like ?
                      or lower(coalesce(m.handicap, '')) like ?
                      or lower(coalesce(pt.pari_type, '')) like ?
                    )
                )
                """
            )
            market_like = f"%{filters.market.lower()}%"
            params.extend([market_like, market_like, market_like])

        rows = self.connection.execute(
            f"""
            select e.event_id, e.match, e.start_ts, e.fin_ts, e.market_count,
                   e.boost_enabled, e.score, e.betradar_id,
                   c.comp_id, c.comp, c.comp_flag,
                   s.sport_id, s.sport, s.sport_shortcut
            from offer_1n2 e
            join comp c on c.comp_id = e.comp_ref
            join sports s on s.sport_id = c.sport_ref
            where {" and ".join(clauses)}
            order by e.start_ts asc, e.event_id asc
            limit ?
            """,
            [*params, filters.limit],
        ).fetchall()

        return [
            self._build_event(
                row,
                market_filter=filters.market,
                max_markets=filters.max_markets_per_event,
            )
            for row in rows
        ]

    def get_event_odds(
        self, event_id: int, market: str | None = None, max_markets: int = 200
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            select e.event_id, e.match, e.start_ts, e.fin_ts, e.market_count,
                   e.boost_enabled, e.score, e.betradar_id,
                   c.comp_id, c.comp, c.comp_flag,
                   s.sport_id, s.sport, s.sport_shortcut
            from offer_1n2 e
            join comp c on c.comp_id = e.comp_ref
            join sports s on s.sport_id = c.sport_ref
            where e.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        return self._build_event(row, market_filter=market, max_markets=max_markets)

    def _build_event(
        self, row: sqlite3.Row, market_filter: str | None, max_markets: int
    ) -> dict[str, Any]:
        markets = self._get_markets(row["event_id"], market_filter, max_markets)
        return {
            "event_id": row["event_id"],
            "event_name": row["match"],
            "sport": {
                "sport_id": row["sport_id"],
                "name": row["sport"],
                "slug": slugify(row["sport"]),
                "shortcut": row["sport_shortcut"],
            },
            "competition": {
                "competition_id": row["comp_id"],
                "name": row["comp"],
                "slug": slugify(row["comp"]),
                "flag": row["comp_flag"],
            },
            "start_time": ts_to_iso(row["start_ts"]),
            "betting_closes_at": ts_to_iso(row["fin_ts"]),
            "score": row["score"],
            "betradar_id": row["betradar_id"],
            "boost_enabled": bool(row["boost_enabled"]),
            "available_market_count": row["market_count"],
            "returned_market_count": len(markets),
            "markets": markets,
        }

    def _get_markets(
        self, event_id: int, market_filter: str | None, max_markets: int
    ) -> list[dict[str, Any]]:
        clauses = ["m.event_id = ?"]
        params: list[Any] = [event_id]
        if market_filter:
            clauses.append(
                """
                (
                  lower(m.lib) like ?
                  or lower(coalesce(m.handicap, '')) like ?
                  or lower(coalesce(pt.pari_type, '')) like ?
                )
                """
            )
            like = f"%{market_filter.lower()}%"
            params.extend([like, like, like])

        market_rows = self.connection.execute(
            f"""
            select m.market_id, m.lib, m.handicap, m.pari_type_ref, m.status_ref,
                   m.index_list, m.pos, m.template_id, m.sort_order,
                   m.boost_enabled, pt.pari_type
            from market m
            left join pari_type pt on pt.pari_type_id = m.pari_type_ref
            where {" and ".join(clauses)}
            order by m.sort_order, m.pos, m.market_id
            limit ?
            """,
            [*params, max_markets],
        ).fetchall()
        if not market_rows:
            return []

        outcome_map = self._get_outcomes([row["market_id"] for row in market_rows])
        return [
            {
                "market_id": row["market_id"],
                "market_name": row["lib"],
                "market_type_id": row["pari_type_ref"],
                "market_type_name": row["pari_type"] or row["lib"],
                "line": row["handicap"],
                "index_list": row["index_list"],
                "position": row["pos"],
                "template_id": row["template_id"],
                "status": row["status_ref"],
                "boost_enabled": bool(row["boost_enabled"]),
                "outcomes": outcome_map.get(row["market_id"], []),
            }
            for row in market_rows
        ]

    def _get_outcomes(self, market_ids: Iterable[int]) -> dict[int, list[dict[str, Any]]]:
        ids = list(market_ids)
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"""
            select market_id, lib, cote, pos, tendance, status, sort_order, team,
                   button_percentage, is_hotbet, outcome_id
            from buttons
            where market_id in ({placeholders})
            order by market_id, sort_order, pos
            """,
            ids,
        ).fetchall()

        outcomes: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            outcomes.setdefault(row["market_id"], []).append(
                {
                    "outcome_id": row["outcome_id"],
                    "label": row["lib"],
                    "team": row["team"],
                    "position": row["pos"],
                    "odds": row["cote"],
                    "trend": row["tendance"],
                    "status": row["status"],
                    "percentage": row["button_percentage"],
                    "is_hotbet": bool(row["is_hotbet"]),
                }
            )
        return outcomes

    def _resolve_sport_id(self, value: str | int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, int) or str(value).isdigit():
            return int(value)

        target = slugify(str(value))
        for sport in self.list_sports():
            candidates = {
                slugify(sport["name"]),
                slugify(sport["shortcut"]),
                str(sport["sport_id"]),
            }
            if target in candidates:
                return int(sport["sport_id"])
        return -1

    def _resolve_competition_id(
        self, value: str | int | None, sport_id: int | None = None
    ) -> int | None:
        if value is None:
            return None
        if isinstance(value, int) or str(value).isdigit():
            return int(value)

        target = slugify(str(value))
        competitions = self.list_competitions(sport_id if sport_id and sport_id > 0 else None)
        for competition in competitions:
            candidates = {
                slugify(competition["name"]),
                str(competition["competition_id"]),
            }
            if target in candidates:
                return int(competition["competition_id"])
        return -1
