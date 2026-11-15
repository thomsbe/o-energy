"""Data completeness handling.

The utility delivers the consumption values of a day with a delay of roughly
24 hours (the values of yesterday arrive around 08:00 today).  Every day
therefore has a state:

``awaiting`` - the delivery time has not been reached yet
``delayed``  - the delivery time has passed but nothing arrived
``partial``  - data arrived but the day is not complete
``final``    - the day is complete and will not change any more
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import DailyStat, Interval, Meter
from oenergy.utils import local_day

STATE_AWAITING = "awaiting"
STATE_DELAYED = "delayed"
STATE_PARTIAL = "partial"
STATE_FINAL = "final"


def expected_delivery_at(day: date, settings: Settings) -> datetime:
    """UTC timestamp when the data of a local day is expected (08:00 local, next day)."""
    tz = ZoneInfo(settings.timezone)
    local = datetime.combine(day + timedelta(days=1), time(8, 0), tzinfo=tz)
    return local.astimezone(UTC).replace(tzinfo=None)


def data_state(day: date, stat: DailyStat | None, now: datetime, settings: Settings) -> str:
    if stat is not None and stat.is_final:
        return STATE_FINAL
    delivery = expected_delivery_at(day, settings)
    if now < delivery:
        return STATE_AWAITING
    if stat is None or not stat.received_slots:
        return STATE_DELAYED
    return STATE_PARTIAL


def recompute(session: Session, settings: Settings, *, lookback_days: int = 30) -> int:
    """Recalculate the daily statistics and mark complete days as final."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()

    days: set[date] = set()
    for offset in range(lookback_days + 1):
        days.add(today - timedelta(days=offset))

    meter_ids = list(session.scalars(select(Interval.meter_id).distinct()))
    if not meter_ids:
        meter_ids = list(session.scalars(select(Meter.id)))
    if not meter_ids:
        return 0
    meter_id = meter_ids[0]

    intervals = list(session.scalars(select(Interval)))
    for interval in intervals:
        day = local_day(interval.slot_start, tz)
        if day:
            days.add(day)

    updated = 0
    for day in sorted(days):
        stat = session.scalar(select(DailyStat).where(DailyStat.day == day, DailyStat.meter_id == meter_id))
        if stat is None:
            stat = DailyStat(day=day, meter_id=meter_id, expected_slots=settings.slots_per_day)
            session.add(stat)

        delivery = expected_delivery_at(day, settings)
        received_slots = stat.received_slots or 0
        complete_by_slots = received_slots >= int(settings.slots_per_day * settings.slot_expected_tolerance)
        complete_by_time = now >= delivery + timedelta(minutes=settings.finalization_grace_minutes)
        has_data = bool(received_slots)

        if not stat.is_final and has_data and (complete_by_slots or complete_by_time):
            stat.is_final = True
            stat.finalized_at = now

        if received_slots == 0:
            stat.is_final = False
            stat.finalized_at = None

        stat.expected_slots = settings.slots_per_day
        stat.updated_at = now
        updated += 1

    session.commit()
    return updated


def completeness_summary(session: Session, settings: Settings, *, days: int = 7) -> list[dict]:
    """State of the last days - used by the dashboard tiles."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()

    rows = {
        stat.day: stat
        for stat in session.scalars(select(DailyStat).where(DailyStat.day >= today - timedelta(days=days)))
    }
    summary = []
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        stat = rows.get(day)
        summary.append(
            {
                "day": day,
                "state": data_state(day, stat, now, settings),
                "kwh": stat.kwh if stat else 0.0,
                "cost_cents": stat.cost_cents if stat else 0.0,
                "received_slots": (stat.received_slots or 0) if stat else 0,
                "expected_slots": settings.slots_per_day,
                "is_final": bool(stat.is_final) if stat else False,
                "finalized_at": stat.finalized_at if stat else None,
                "delivery_expected_at": expected_delivery_at(day, settings),
            }
        )
    return summary
