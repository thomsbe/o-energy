from __future__ import annotations

from datetime import datetime, timedelta

from oenergy.config import Settings
from oenergy.db.models import DailyStat, Meter, MeterReading, ReadingWindow
from oenergy.services.backfill import (
    Window,
    merge_windows,
    plan_queries,
    record_query,
    subtract,
)


def _settings() -> Settings:
    return Settings(
        db_url="sqlite:///:memory:",
        timezone="Europe/Berlin",
        slot_minutes=15,
        readings_backfill_days=3,
        readings_history_days=10,
        readings_max_window_days=7,
        gap_retry_minutes=60,
    )


def test_subtract_removes_covered_parts() -> None:
    target = Window(datetime(2026, 11, 1), datetime(2026, 11, 10))
    gaps = subtract(target, [Window(datetime(2026, 11, 3), datetime(2026, 11, 5))])

    assert gaps == [
        Window(datetime(2026, 11, 1), datetime(2026, 11, 3)),
        Window(datetime(2026, 11, 5), datetime(2026, 11, 10)),
    ]


def test_merge_windows_joins_overlapping_ranges() -> None:
    merged = merge_windows(
        [
            Window(datetime(2026, 11, 1), datetime(2026, 11, 3)),
            Window(datetime(2026, 11, 2), datetime(2026, 11, 4)),
            Window(datetime(2026, 11, 6), datetime(2026, 11, 7)),
        ]
    )

    assert merged == [
        Window(datetime(2026, 11, 1), datetime(2026, 11, 4)),
        Window(datetime(2026, 11, 6), datetime(2026, 11, 7)),
    ]


def test_first_sync_plans_the_whole_history(session) -> None:
    session.add(Meter(id="m"))
    session.commit()

    windows = plan_queries(session, "m", _settings())

    assert windows
    assert all(window.start < window.end for window in windows)


def test_covered_range_is_not_queried_again(session) -> None:
    settings = _settings()
    now = datetime.utcnow()
    session.add(Meter(id="m"))
    session.commit()

    # readings every 15 minutes for the last two days
    for index in range(2 * 96):
        session.add(
            MeterReading(
                meter_id="m",
                register_obis_code="1-0:1.8.0",
                read_at=now - timedelta(days=2) + timedelta(minutes=15 * index),
                value=100.0 + index * 0.1,
            )
        )
    # the days are complete -> they will not be requested again
    for offset in (1, 2):
        session.add(
            DailyStat(
                meter_id="m",
                day=(now - timedelta(days=offset)).date(),
                received_slots=96,
                expected_slots=96,
                is_final=True,
            )
        )
    session.commit()

    covered = Window(now - timedelta(days=2), now - timedelta(days=1))
    windows = plan_queries(session, "m", settings, now)

    # the days that are complete are never requested again
    assert not any(window.overlaps(covered) for window in windows)


def test_recently_queried_windows_are_not_repeated(session) -> None:
    settings = _settings()
    now = datetime.utcnow()
    session.add(Meter(id="m"))
    session.commit()

    queried = Window(now - timedelta(hours=6), now)
    record_query(session, "m", queried, rows_found=0)

    windows = plan_queries(session, "m", settings, now)

    assert not any(window.overlaps(queried) for window in windows)


def test_empty_window_is_retried_after_the_retry_interval(session) -> None:
    settings = _settings()
    now = datetime.utcnow()
    session.add(Meter(id="m"))
    session.commit()

    window = Window(now - timedelta(days=1), now)
    record_query(session, "m", window, rows_found=0)
    session.query(ReadingWindow).update({"queried_at": now - timedelta(minutes=120)})
    session.commit()

    windows = plan_queries(session, "m", settings, now)

    assert any(item.overlaps(window) for item in windows)
