from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oenergy.config import get_settings
from oenergy.db.base import Database, set_database


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("POLL_INTERVAL_MINUTES", "15")
    monkeypatch.setenv("READINGS_BACKFILL_DAYS", "3")
    monkeypatch.setenv("SLOT_MINUTES", "15")
    monkeypatch.setenv("TIMEZONE", "Europe/Berlin")
    monkeypatch.setenv("ENERGY_UNIT", "auto")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(f"sqlite:///{tmp_path / 'test.db'}")
    db.create_all()
    set_database(db)
    yield db


@pytest.fixture
def session(database: Database):
    with database.session() as db_session:
        yield db_session


@pytest.fixture
def seeded(session, database) -> dict:
    from oenergy.services.demo import seed

    return seed(session, get_settings(), days=10)


@pytest.fixture
def client(database: Database) -> Iterator[TestClient]:
    from oenergy.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
