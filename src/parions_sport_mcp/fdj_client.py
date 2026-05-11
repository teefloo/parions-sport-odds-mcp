from __future__ import annotations

import io
import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import httpx

from .errors import InvalidSourceDataError, RateLimitedError, SourceUnavailableError
from .repository import OddsRepository

LOGGER = logging.getLogger(__name__)

DEFAULT_OFFER_ZIP_URL = (
    "https://www.pointdevente.parionssport.fdj.fr"
    "/service-sport-pointdevente-bff/v1/files/spdv_mobile_offre.sqlite.zip"
)
DEFAULT_TTL_SECONDS = 120


@dataclass
class CacheMetadata:
    source_url: str
    downloaded_at: str | None
    expires_at: str | None
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    cache_path: str | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FDJOfferStore:
    """Download, cache, validate, and expose the official FDJ offer database."""

    def __init__(
        self,
        source_url: str = DEFAULT_OFFER_ZIP_URL,
        cache_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = 20.0,
        http_client: httpx.Client | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self.source_url = source_url
        self.cache_dir = Path(cache_dir or self._default_cache_dir()).expanduser()
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.now_func = now_func or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls) -> "FDJOfferStore":
        return cls(
            source_url=os.getenv("PARIONS_SPORT_OFFER_URL", DEFAULT_OFFER_ZIP_URL),
            cache_dir=os.getenv("PARIONS_SPORT_CACHE_DIR"),
            ttl_seconds=int(os.getenv("PARIONS_SPORT_CACHE_TTL_SECONDS", "120")),
            timeout_seconds=float(os.getenv("PARIONS_SPORT_TIMEOUT_SECONDS", "20")),
        )

    def get_connection(self) -> tuple[sqlite3.Connection, CacheMetadata, list[str]]:
        """Return an in-memory SQLite connection and cache metadata."""

        with self._lock:
            warnings: list[str] = []
            try:
                self._ensure_current()
            except (SourceUnavailableError, InvalidSourceDataError) as exc:
                if self.database_path.exists():
                    LOGGER.warning("Using stale FDJ offer cache: %s", exc)
                    warnings.append("STALE_CACHE_USED")
                else:
                    raise

            metadata = self._read_metadata()
            stale = not self._is_current(metadata)
            if stale and "STALE_CACHE_USED" not in warnings:
                warnings.append("STALE_CACHE_USED")
            metadata.stale = stale

            connection = self._open_in_memory_database()
            return connection, metadata, warnings

    @property
    def database_path(self) -> Path:
        return self.cache_dir / "spdv_mobile_offre.sqlite"

    @property
    def metadata_path(self) -> Path:
        return self.cache_dir / "metadata.json"

    def _default_cache_dir(self) -> Path:
        base = os.getenv("XDG_CACHE_HOME")
        if base:
            return Path(base) / "parions-sport-mcp"
        return Path.home() / ".cache" / "parions-sport-mcp"

    def _ensure_current(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._read_metadata()
        if (
            self.database_path.exists()
            and metadata.source_url == self.source_url
            and self._is_current(metadata)
        ):
            return

        headers = {
            "Accept": "application/zip, application/octet-stream;q=0.9, */*;q=0.1",
            "User-Agent": "parions-sport-mcp/0.1.0",
        }
        if metadata.etag:
            headers["If-None-Match"] = metadata.etag
        if metadata.last_modified:
            headers["If-Modified-Since"] = metadata.last_modified

        client = self.http_client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self.http_client is None
        try:
            response = client.get(self.source_url, headers=headers)
        except httpx.HTTPError as exc:
            raise SourceUnavailableError(f"Could not reach FDJ offer ZIP: {exc}") from exc
        finally:
            if close_client:
                client.close()

        if response.status_code == 304 and self.database_path.exists():
            metadata.downloaded_at = self._now_iso()
            metadata.expires_at = self._expires_at(response.headers)
            metadata.source_url = self.source_url
            metadata.cache_path = str(self.database_path)
            self._write_metadata(metadata)
            return

        if response.status_code in {403, 429}:
            raise RateLimitedError(
                f"FDJ offer ZIP returned HTTP {response.status_code}; retry later"
            )
        if response.status_code >= 400:
            raise SourceUnavailableError(
                f"FDJ offer ZIP returned HTTP {response.status_code}"
            )

        self._store_zip_payload(response.content)
        metadata = CacheMetadata(
            source_url=self.source_url,
            downloaded_at=self._now_iso(),
            expires_at=self._expires_at(response.headers),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            content_length=len(response.content),
            cache_path=str(self.database_path),
            stale=False,
        )
        self._write_metadata(metadata)

    def _store_zip_payload(self, payload: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = [
                    name
                    for name in archive.namelist()
                    if name.lower().endswith((".sqlite", ".db"))
                ]
                if not members:
                    raise InvalidSourceDataError(
                        "FDJ offer ZIP did not contain a SQLite database"
                    )
                database_bytes = archive.read(members[0])
        except zipfile.BadZipFile as exc:
            raise InvalidSourceDataError("FDJ offer payload is not a valid ZIP") from exc

        fd, temp_path = tempfile.mkstemp(
            prefix="spdv_mobile_offre-", suffix=".sqlite", dir=self.cache_dir
        )
        os.close(fd)
        temp = Path(temp_path)
        try:
            temp.write_bytes(database_bytes)
            with sqlite3.connect(f"file:{temp}?mode=ro", uri=True) as connection:
                OddsRepository.assert_schema(connection)
            temp.replace(self.database_path)
        except sqlite3.DatabaseError as exc:
            raise InvalidSourceDataError(
                "FDJ offer ZIP did not contain a readable SQLite database"
            ) from exc
        finally:
            if temp.exists():
                temp.unlink()

    def _open_in_memory_database(self) -> sqlite3.Connection:
        if not self.database_path.exists():
            raise SourceUnavailableError("No cached FDJ offer database is available")

        try:
            disk = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
            memory = sqlite3.connect(":memory:")
            disk.backup(memory)
            disk.close()
            memory.row_factory = sqlite3.Row
            OddsRepository.assert_schema(memory)
            return memory
        except sqlite3.DatabaseError as exc:
            raise InvalidSourceDataError(
                "Cached FDJ offer database is not readable"
            ) from exc

    def _read_metadata(self) -> CacheMetadata:
        if not self.metadata_path.exists():
            return CacheMetadata(
                source_url=self.source_url,
                downloaded_at=None,
                expires_at=None,
                cache_path=str(self.database_path),
            )
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CacheMetadata(
                source_url=self.source_url,
                downloaded_at=None,
                expires_at=None,
                cache_path=str(self.database_path),
            )
        return CacheMetadata(
            source_url=data.get("source_url", self.source_url),
            downloaded_at=data.get("downloaded_at"),
            expires_at=data.get("expires_at"),
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            content_length=data.get("content_length"),
            cache_path=data.get("cache_path", str(self.database_path)),
            stale=bool(data.get("stale", False)),
        )

    def _write_metadata(self, metadata: CacheMetadata) -> None:
        self.metadata_path.write_text(
            json.dumps(metadata.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _is_current(self, metadata: CacheMetadata) -> bool:
        if not metadata.expires_at:
            return False
        try:
            expires_at = datetime.fromisoformat(
                metadata.expires_at.replace("Z", "+00:00")
            )
        except ValueError:
            return False
        return self.now_func() < expires_at

    def _expires_at(self, headers: httpx.Headers) -> str:
        max_age = self.ttl_seconds
        cache_control = headers.get("cache-control", "")
        match = re.search(r"max-age=(\d+)", cache_control)
        if match:
            max_age = int(match.group(1))
        return (self.now_func() + timedelta(seconds=max_age)).isoformat().replace(
            "+00:00", "Z"
        )

    def _now_iso(self) -> str:
        return self.now_func().isoformat().replace("+00:00", "Z")
