from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from oenergy.config import get_settings, resolve_db_path


def utcnow() -> datetime:
    """Naive UTC timestamp - the single time representation used in the database."""
    return datetime.now(UTC).replace(tzinfo=None)


def _build_engine(db_url: str) -> Engine:
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True, future=True)

    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


class Database:
    """Thin wrapper around the SQLAlchemy engine with a scoped session factory."""

    def __init__(self, db_url: str | None = None) -> None:
        settings = get_settings()
        self.url = resolve_db_path(db_url or settings.db_url)
        self.engine: Engine = _build_engine(self.url)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    @property
    def sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if self.url.startswith(prefix):
            return Path(self.url[len(prefix) :])
        return None

    def session(self) -> Session:
        return self.session_factory()

    def create_all(self) -> None:
        from oenergy.db import models

        models.Base.metadata.create_all(self.engine)


_database: Database | None = None


def get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def set_database(database: Database) -> None:
    """Override the process wide database (used by the CLI and by tests)."""
    global _database
    _database = database


def get_session() -> Iterator[Session]:
    database = get_database()
    session = database.session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
