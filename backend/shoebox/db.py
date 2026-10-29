"""SQLite engine, sessions, and schema migrations."""

from __future__ import annotations

import fcntl
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, select

from .config import get_settings

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

BUILTIN_EVENT_KINDS: list[tuple[str, int | None, int | None]] = [
    ("Christmas", 12, 25),
    ("Christmas Eve", 12, 24),
    ("New Year", 1, 1),
    ("Easter", None, None),
    ("Halloween", 10, 31),
    ("Thanksgiving", None, None),
    ("Fourth of July", 7, 4),
    ("Birthday", None, None),
    ("Wedding", None, None),
    ("Graduation", None, None),
    ("Vacation", None, None),
    ("Other", None, None),
]


def _set_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    event.listen(engine, "connect", _set_pragmas)
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine(), expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{get_settings().db_path}")
    return cfg


def current_revision() -> str | None:
    with get_engine().connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def migrate() -> None:
    """Upgrade the schema to head, snapshotting an existing DB first.

    Both the API and worker call this at startup; a file lock serializes them.
    """
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with open(settings.data_dir / ".migrate.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current, head = current_revision(), head_revision()
        if current != head:
            if current is not None:
                from .backup import pre_upgrade_snapshot

                path = pre_upgrade_snapshot(current)
                log.info("snapshot before upgrade %s -> %s: %s", current, head, path)
            command.upgrade(alembic_config(), "head")
        seed()


def seed() -> None:
    from .models import EventKind, Photo

    with session_scope() as s:
        existing = {k.name for k in s.exec(select(EventKind)).all()}
        for name, month, day in BUILTIN_EVENT_KINDS:
            if name not in existing:
                s.add(EventKind(name=name, month=month, day=day, builtin=True))
        if get_meta(s, "db_id") is None:
            # Identifies this database in truth.json, so a fresh one can't overwrite another's.
            set_meta(s, "db_id", uuid.uuid4().hex)
            if s.exec(select(Photo.id).limit(1)).first() is not None:
                # An existing database wrote any truth.json from before ids existed.
                set_meta(s, "truth_adopted", "legacy")


def same(column, value):
    """SQL test that a column still holds a value read earlier (NULL-safe: ``IS``)."""
    return column.is_not_distinct_from(value)


def update_if(session: Session, model, row_id: int, *conditions, **values) -> bool:
    """One atomic UPDATE of a row, only if ``conditions`` still hold. True if it wrote.

    Worker jobs read a row, spend seconds on OCR or detection outside any transaction,
    then write. Checking in the UPDATE itself means an edit the user made in between wins.
    """
    stmt = (update(model).where(model.id == row_id, *conditions).values(**values)
            .execution_options(synchronize_session=False))
    return session.exec(stmt).rowcount == 1


def get_meta(session: Session, key: str, default=None):
    from .models import Meta

    row = session.get(Meta, key)
    return default if row is None else row.value


def set_meta(session: Session, key: str, value) -> None:
    from .models import Meta

    if isinstance(value, datetime):
        value = value.isoformat()
    row = session.get(Meta, key)
    if row is None:
        session.add(Meta(key=key, value=value))
    else:
        row.value = value
        session.add(row)
