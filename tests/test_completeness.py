from __future__ import annotations

from datetime import date, datetime

from oenergy.config import Settings
from oenergy.db.models import DailyStat, Meter
from oenergy.services.completeness import (
    STATE_AWAITING,
    STATE_DELAYED,
    STATE_FINAL,
    STATE_PARTIAL,
    data_state,
    expected_delivery_at,
    recompute,
)


def _settings() -> Settings:
    return Settings(
        db_url="sqlite:///:memory:",
        timezone="Europe/Berlin",
        slot_minutes=15,
        finalization_grace_minutes=120,
    )


def test_delivery_is_expected_around_8am_the_next_day() -> None:
    settings = _settings()
    delivery = expected_delivery_at(date(2026, 11, 14), settings)

    # 08:00 local time in November is CET (+01:00) -> 07:00 UTC
    assert delivery == datetime(2026, 11, 15, 7, 0)


def test_state_before_delivery_is_awaiting() -> None:
    settings = _settings()
    day = date(2026, 11, 14)
    assert data_state(day, None, datetime(2026, 11, 15, 6, 0), settings) == STATE_AWAITING


def test_state_after_delivery_without_data_is_delayed() -> None:
    settings = _settings()
    day = date(2026, 11, 14)
    assert data_state(day, None, datetime(2026, 11, 15, 9, 0), settings) == STATE_DELAYED


def test_state_with_partial_data_is_partial() -> None:
    settings = _settings()
    day = date(2026, 11, 14)
    stat = DailyStat(day=day, meter_id="m", received_slots=40, is_final=False)
    assert data_state(day, stat, datetime(2026, 11, 15, 9, 0), settings) == STATE_PARTIAL


def test_state_of_final_day_is_final() -> None:
    settings = _settings()
    day = date(2026, 11, 14)
    stat = DailyStat(day=day, meter_id="m", received_slots=96, is_final=True)
    assert data_state(day, stat, datetime(2026, 11, 15, 9, 0), settings) == STATE_FINAL


def test_recompute_marks_old_days_as_final(session) -> None:
    from datetime import timedelta

    settings = _settings()
    now = datetime.utcnow()
    day = now.date() - timedelta(days=3)
    session.add(Meter(id="m"))
    stat = DailyStat(day=day, meter_id="m", received_slots=90, expected_slots=96)
    session.add(stat)
    session.commit()

    recompute(session, settings)

    session.refresh(stat)
    assert stat.is_final is True
    assert stat.finalized_at is not None


def test_recompute_keeps_recent_days_provisional(session) -> None:
    from datetime import timedelta

    settings = _settings()
    now = datetime.utcnow()
    day = now.date() - timedelta(days=1)
    session.add(Meter(id="m"))
    stat = DailyStat(day=day, meter_id="m", received_slots=90, expected_slots=96)
    session.add(stat)
    session.commit()

    recompute(session, settings)

    session.refresh(stat)
    # yesterday is only delivered around 08:00 - before that it stays provisional
    if datetime.utcnow() < datetime.combine(day + timedelta(days=1), datetime.min.time()):
        assert stat.is_final is False
    else:
        assert stat.is_final is True
