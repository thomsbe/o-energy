from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Agreement, DailyStat, Interval, Price
from oenergy.services.pricing import RateWindow, compose_at, group_rates, lookup_rate
from oenergy.utils import days_in_month, local_day

logger = logging.getLogger(__name__)

DEFAULT_TZ = ZoneInfo("Europe/Berlin")


@dataclass
class PricePoint:
    """A price that can be applied to a consumption slot."""

    net_ct_per_kwh: float | None = None
    gross_ct_per_kwh: float | None = None
    scope: str = "customer"
    source: str = "agreement_rates"
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class PriceBook:
    """In-memory price lookup for a time window.

    The customer price is the sum of all rates that are valid in a window
    (base component + variable component of the dynamic tariff).
    """

    def __init__(
        self,
        session: Session,
        start: datetime,
        end: datetime,
        vat: float = 0.19,
        tz: ZoneInfo = DEFAULT_TZ,
    ) -> None:
        rows = list(
            session.scalars(
                select(Price).where(Price.starts_at < end, Price.ends_at > start).order_by(Price.starts_at)
            )
        )
        self._customer_rows = [row for row in rows if row.scope != "epex"]
        self._epex_rows = [row for row in rows if row.scope == "epex"]
        self._vat = vat
        self._tz = tz
        self._windows = group_rates(self._customer_rows, vat)
        self._static_rows = [row for row in self._customer_rows if row.source == "static"]

    @staticmethod
    def _lookup(rows: list[Price], at: datetime) -> Price | None:
        return lookup_rate(rows, at)

    def _covering_window(self, at: datetime) -> RateWindow | None:
        """The most specific window that covers ``at`` (binary search)."""
        if not self._windows:
            return None
        starts = [window.starts_at for window in self._windows]
        index = bisect_right(starts, at) - 1
        for window in reversed(self._windows[: index + 1]):
            if window.covers(at):
                return window
        return None

    def at(self, at: datetime) -> PricePoint | None:
        """The effective customer price, falling back to EPEX plus surcharge."""
        window = self._covering_window(at)
        if window is not None and window.gross_ct:
            return PricePoint(
                net_ct_per_kwh=window.net_ct,
                gross_ct_per_kwh=window.gross_ct,
                scope="customer",
                source=window.source,
                starts_at=window.starts_at,
                ends_at=window.ends_at,
            )

        effective = compose_at(
            self._static_rows,
            self._epex_rows,
            at,
            vat=self._vat,
            tz=self._tz,
        )
        if effective is not None and effective.gross_ct:
            return PricePoint(
                net_ct_per_kwh=effective.net_ct,
                gross_ct_per_kwh=effective.gross_ct,
                scope="epex" if effective.source == "epex" else "customer",
                source=effective.source,
                starts_at=effective.starts_at,
                ends_at=effective.ends_at,
            )

        epex = self._lookup(self._epex_rows, at)
        if epex is None:
            return None
        net = epex.net_ct_per_kwh
        return PricePoint(
            net_ct_per_kwh=net,
            gross_ct_per_kwh=net * (1 + self._vat) if net is not None else None,
            scope="epex",
            source="epex",
            starts_at=epex.starts_at,
            ends_at=epex.ends_at,
        )


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
    logger.info("Computed costs for %s days", updated)
    return updated
