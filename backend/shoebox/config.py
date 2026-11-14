"""Runtime configuration.

Values come from environment variables (set in private/.env), optionally
overridden by a TOML file at $SHOEBOX_CONFIG (default /config/config.toml).
"""

from __future__ import annotations

import os
import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, replace
from functools import lru_cache
from pathlib import Path
from typing import Iterator

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".heic", ".heif"}


@dataclass
class Settings:
    photos_root: Path = Path("/photos")
    data_dir: Path = Path("/data")
    backup_dir: Path = Path("/backup")
    # Regex applied to a file's stem (name without extension). A match marks the
    # file as a back scan; the "stem" group must equal its front's stem.
    back_pattern: str = r"^(?P<stem>.+?)[ _-](back|b|reverse)$"
    # Regex on a file stem capturing years in the name (groups "a" and optional "b"),
    # e.g. Album3_1984_1986_0161 -> estimated 1984-1986. Empty string disables.
    name_year_pattern: str = r"_(?P<a>(?:19|20)\d{2})(?:_(?P<b>(?:19|20)\d{2}))?_\d+(?:[ _-](?:back|b|reverse))?$"
    # A loose back (no front of its own) dates fronts within this many files of it.
    loose_back_window: int = 3
    # Dates read off backs outside this range are treated as misreads.
    ocr_min_year: int = 1940
    ocr_max_year: int = 2015
    # Offset written to OffsetTimeOriginal on export, e.g. "-05:00".
    export_tz_offset: str = "-05:00"
    snapshot_keep: int = 10  # newest snapshots always kept; older ones thin out by hour/day/week/month
    truth_history_keep: int = 50
    thumb_size: int = 400
    preview_size: int = 1600
    # Hamming distance on 64-bit pHash at or below which two photos are duplicates.
    dup_phash_distance: int = 4
    fixity_interval_days: int = 7
    # Face detector score margin required before auto-rotating a scan.
    orient_min_score: float = 0.6
    orient_margin: float = 0.25
    model_dir: Path = Path("/models")
    # Offline list of the world's towns (GeoNames), for city-centre map coordinates on export.
    geonames_dir: Path = Path("/geo")
    # Address the server is published on (SHOEBOX_BIND, also used by compose). Anything other than
    # loopback means other devices on the home network may connect: see app._is_allowed_host.
    bind: str = "127.0.0.1"
    # Extra host names the server answers to on the network, comma-separated (SHOEBOX_HOSTS).
    hosts: str = ""
    # Collections: the photos at photos_root are the main one, named collection_name. Further,
    # fully separate collections (slots 2..MAX_SLOT) are mounted under collections_root/<n>/photos
    # and /backup by compose, from COLLECTION_<n>_NAME / _PHOTOS / _BACKUP in private/.env.
    collection_name: str = "Photos"
    collections_root: Path = Path("/collections")
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "shoebox.db"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"


def _coerce(value: str, current):
    if isinstance(current, Path):
        return Path(value)
    if isinstance(current, bool):
        return value.lower() in {"1", "true", "yes"}
    if isinstance(current, int):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


def load_settings() -> Settings:
    s = Settings()
    config_path = Path(os.environ.get("SHOEBOX_CONFIG", "/config/config.toml"))
    file_values: dict = {}
    if config_path.is_file():
        with config_path.open("rb") as fh:
            file_values = tomllib.load(fh)
    for f in fields(s):
        if f.name == "extra":
            continue
        current = getattr(s, f.name)
        if f.name in file_values:
            setattr(s, f.name, _coerce(str(file_values[f.name]), current))
        env = os.environ.get(f"SHOEBOX_{f.name.upper()}")
        if env is not None:
            setattr(s, f.name, _coerce(env, current))
    return s


# --- collections --------------------------------------------------------------------------
# Each collection is a separate set of photos with its own database, thumbnails, backups,
# truth.json and exports: no people, dates, faces or edits are shared between collections.

MAIN = "main"
MAX_SLOT = 5  # main + collections 2..5; compose mounts that many slots
CURRENT_COLLECTION: ContextVar[str] = ContextVar("shoebox_collection", default=MAIN)


@dataclass(frozen=True)
class Collection:
    id: str  # "main", or "c<slot>": also its folder under data_dir/collections
    name: str
    slot: int


def collections() -> list[Collection]:
    """The configured collections: main, then each slot whose photo and backup folders are set."""
    out = [Collection(MAIN, _base_settings().collection_name, 1)]
    for n in range(2, MAX_SLOT + 1):
        if os.environ.get(f"COLLECTION_{n}_PHOTOS") and os.environ.get(f"COLLECTION_{n}_BACKUP"):
            out.append(Collection(f"c{n}", os.environ.get(f"COLLECTION_{n}_NAME") or f"Collection {n}", n))
    return out


def collection_ids() -> set[str]:
    return {c.id for c in collections()}


@contextmanager
def use_collection(collection_id: str) -> Iterator[None]:
    """Run the enclosed code (settings, database, jobs) against one collection."""
    token = CURRENT_COLLECTION.set(collection_id)
    try:
        yield
    finally:
        CURRENT_COLLECTION.reset(token)


@lru_cache
def _base_settings() -> Settings:
    return load_settings()


@lru_cache
def _collection_settings(collection_id: str) -> Settings:
    base = _base_settings()
    slot = int(collection_id[1:])
    root = base.collections_root / str(slot)
    return replace(base, photos_root=root / "photos", backup_dir=root / "backup",
                   data_dir=base.data_dir / "collections" / collection_id,
                   collection_name=os.environ.get(f"COLLECTION_{slot}_NAME") or f"Collection {slot}")


def get_settings() -> Settings:
    """Settings for the collection this code runs for (CURRENT_COLLECTION)."""
    cid = CURRENT_COLLECTION.get()
    return _base_settings() if cid == MAIN else _collection_settings(cid)


def _clear() -> None:
    _base_settings.cache_clear()
    _collection_settings.cache_clear()


get_settings.cache_clear = _clear  # type: ignore[attr-defined]
