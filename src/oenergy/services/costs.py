from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Agreement, DailyStat, Interval
from oenergy.services.pricing import PriceBook, PricePoint
from oenergy.utils import days_in_month, local_day

DEFAULT_TZ = ZoneInfo("Europe/Berlin")


def _gross_price(price: PricePoint | None, vat: float) -> float | None:
    if price is None:
        return None
    if price.gross_ct_per_kwh is not None:
        return price.gross_ct_per_kwh
    if price.net_ct_per_kwh is not None:
        return price.net_ct_per_kwh * (1 + vat)
    return None


def compute_costs(session: Session, settings: Settings) -> int:
    """Price every consumption slot and roll the costs up per day."""
    tz = ZoneInfo(settings.timezone)
    from oenergy.services.account import active_meter_id

    meter_id = active_meter_id(session, settings)
    interval_query = select(Interval).order_by(Interval.slot_start)
    if meter_id is not None:
        interval_query = interval_query.where(Interval.meter_id == meter_id)
    intervals = list(session.scalars(interval_query))
    if not intervals:
        return 0

    agreement = session.scalar(select(Agreement).order_by(Agreement.updated_at.desc()))
    vat = float(agreement.vat_rate or 0.19) if agreement else 0.19
    gross_standing_ct = float(agreement.gross_monthly_standing_charge_ct or 0.0) if agreement else 0.0

    start = intervals[0].slot_start
    end = intervals[-1].slot_end + timedelta(days=1)
    book = PriceBook(session, start, end)

    per_day: dict[object, dict[str, float]] = {}
    for interval in intervals:
        day = local_day(interval.slot_start, tz)
        if day is None:
            continue
        bucket = per_day.setdefault(day, {"kwh": 0.0, "energy_cost": 0.0, "slots": 0})
        bucket["kwh"] += interval.kwh
        bucket["slots"] += 1
        price = book.at(interval.slot_start)
        gross = _gross_price(price, vat)
        if gross is not None:
            bucket["energy_cost"] += interval.kwh * gross

    updated = 0
    for day, bucket in per_day.items():
        stat = session.scalar(select(DailyStat).where(DailyStat.day == day))
        if stat is None:
            stat = DailyStat(day=day, meter_id=intervals[0].meter_id)
            session.add(stat)
        stat.kwh = bucket["kwh"]
        stat.energy_cost_cents = bucket["energy_cost"]
        standing = gross_standing_ct / days_in_month(day) if gross_standing_ct else 0.0
        stat.standing_cost_cents = standing
        stat.cost_cents = bucket["energy_cost"] + standing
        stat.received_slots = int(bucket["slots"])
        stat.expected_slots = settings.slots_per_day
        stat.updated_at = datetime.utcnow()
        updated += 1

    session.commit()
    logger.info("Computed costs for {} days", updated)
    return updated
