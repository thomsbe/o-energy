from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.client import KrakenClient
from oenergy.config import Settings
from oenergy.db.models import Interval, Meter, MeterReading
from oenergy.utils import floor_slot, parse_datetime, parse_decimal

REBUILD_WINDOW_DAYS = 120


@dataclass
class Slot:
    slot_start: datetime
    slot_end: datetime
    kwh: float
    n_readings: int


@dataclass
class RawReading:
    read_at: datetime
    value: float
    register_obis_code: str
    type_of_read: str | None
    origin: str | None
    status: str | None


def detect_unit(deltas: list[float], configured: str = "auto") -> str:
    """Decide whether the register values are kWh or Wh."""
    if configured in ("kwh", "wh"):
        return configured
    positive = [delta for delta in deltas if delta > 0]
    if not positive:
        return "kwh"
    median = statistics.median(positive)
    # more than 100 units per slot is unrealistic for kWh -> Wh
    return "wh" if median > 100 else "kwh"


def derive_intervals(
    readings: list[RawReading],
    *,
    slot_minutes: int = 15,
    unit: str = "auto",
    mode: str = "cumulative",
) -> list[Slot]:
    """Turn a series of register readings into per slot consumption.

    ``mode="cumulative"`` treats the readings as a cumulative register (the
    difference of two readings is the consumption of the period between them),
    ``mode="interval"`` treats every reading as the consumption of its slot.
    ``mode="auto"`` decides based on the share of rising values.
    """
    if not readings:
        return []

    ordered = sorted(readings, key=lambda r: r.read_at)
    deltas: list[float] = [current.value - previous.value for previous, current in pairwise(ordered)]

    if mode == "auto":
        rising = sum(1 for delta in deltas if delta >= 0)
        cumulative = deltas and rising / len(deltas) >= 0.8
    else:
        cumulative = mode == "cumulative"

    # the reporting interval is the median distance between two readings
    gaps = [(current.read_at - previous.read_at).total_seconds() for previous, current in pairwise(ordered)]
    gaps.sort()

    # unit detection works on the consumption per slot, not on the raw delta
    per_slot_deltas: list[float] = []
    for (previous, current), delta in zip(pairwise(ordered), deltas, strict=True):
        span = max(1, round((current.read_at - previous.read_at).total_seconds() / (slot_minutes * 60)))
        if delta > 0:
            per_slot_deltas.append(delta / span)

    resolved_unit = detect_unit(per_slot_deltas, unit)
    factor = 1000.0 if resolved_unit == "wh" else 1.0

    buckets: dict[datetime, Slot] = {}

    if cumulative:
        reset_pending = False
        for previous, current in pairwise(ordered):
            delta = (current.value - previous.value) / factor
            if delta < 0:
                # the meter was replaced: the difference is not consumption and
                # the next difference would span the old and the new register
                reset_pending = True
                continue
            if reset_pending:
                reset_pending = False
                continue
            gap = current.read_at - previous.read_at
            span = max(1, round(gap.total_seconds() / (slot_minutes * 60)))
            # the consumption between two readings belongs to the slot that ends
            # with the later reading
            slot = floor_slot(current.read_at - timedelta(seconds=1), slot_minutes)
            per_slot = delta / span
            for index in range(span):
                slot_start = slot - timedelta(minutes=slot_minutes * index)
                entry = buckets.get(slot_start)
                if entry is None:
                    entry = Slot(slot_start, slot_start + timedelta(minutes=slot_minutes), 0.0, 0)
                    buckets[slot_start] = entry
                entry.kwh += per_slot
                entry.n_readings += 1
    else:
        for reading in ordered:
            slot = floor_slot(reading.read_at, slot_minutes)
            entry = buckets.get(slot)
            if entry is None:
                entry = Slot(slot, slot + timedelta(minutes=slot_minutes), 0.0, 0)
                buckets[slot] = entry
            entry.kwh += reading.value / factor
            entry.n_readings += 1

    return [buckets[key] for key in sorted(buckets)]


def sync_readings(session: Session, client: KrakenClient, settings: Settings) -> int:
    """Fetch the consumption intervals of the property (delivered with ~24h delay).

    The values come from the smart meter gateway via
    ``property.measurements(readingFrequencyType: RAW_INTERVAL)``.  Only time
    ranges that are not covered yet are requested - see
    :mod:`oenergy.services.backfill`.
    """
    from oenergy.services.account import primary_property
    from oenergy.services.backfill import plan_queries, record_query

    prop = primary_property(session, settings)
    if prop is None:
        logger.warning("No property known - cannot sync the consumption data")
        return 0

    meter_key = prop.meter_id or prop.id
    now = datetime.utcnow()
    stored = 0
    errors: list[str] = []
    meter_row = session.get(Meter, meter_key)
    known_start = _known_start(session, meter_key)

    for window in plan_queries(session, meter_key, settings, now):
        try:
            nodes = list(
                client.paginate(
                    queries.PROPERTY_MEASUREMENTS,
                    {
                        "propertyId": prop.id,
                        "startAt": window.start.isoformat() + "Z",
                        "endAt": window.end.isoformat() + "Z",
                        "timezone": settings.timezone,
                    },
                    edges_path=("property", "measurements"),
                )
            )
        except Exception as exc:
            logger.warning(
                "Property measurements failed (%s - %s): %s",
                window.start,
                window.end,
                exc,
            )
            errors.append(f"{window.start:%d.%m. %H:%M}-{window.end:%d.%m. %H:%M}: {exc}")
            continue

        rows = []
        for node in nodes:
            slot_start = parse_datetime(node.get("startAt"))
            value = parse_decimal(node.get("value"))
            if slot_start is None or value is None:
                continue
            slot_end = parse_datetime(node.get("endAt")) or (
                slot_start + timedelta(minutes=settings.slot_minutes)
            )
            rows.append(
                {
                    "meter_id": meter_key,
                    "slot_start": slot_start,
                    "slot_end": slot_end,
                    "kwh": value,
                    "source": "measured",
                    "n_readings": 1,
                    "received_at": datetime.utcnow(),
                }
            )

        if rows:
            statement = sqlite_insert(Interval).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=["meter_id", "slot_start"],
                set_={
                    "slot_end": statement.excluded.slot_end,
                    "kwh": statement.excluded.kwh,
                    "n_readings": statement.excluded.n_readings,
                    "source": statement.excluded.source,
                },
            )
            session.execute(statement)
            session.commit()
            stored += len(rows)

        record_query(session, meter_key, window, len(rows))
        logger.info(
            "Meter {}: window {} -> {} fetched {} intervals",
            meter_key,
            window.start,
            window.end,
            len(rows),
        )

        if rows:
            earliest = min(row["slot_start"] for row in rows)
            if meter_row is not None and (
                meter_row.history_start is None or earliest < meter_row.history_start
            ):
                meter_row.history_start = earliest
                meter_row.updated_at = datetime.utcnow()
                session.commit()
        elif meter_row is not None and known_start is not None and window.end <= known_start:
            # vor dem frühesten bekannten Wert gibt es keine Daten mehr
            if not meter_row.history_exhausted:
                logger.info("Historie vollständig erfasst - kein Wert vor {}", window.start)
            meter_row.history_exhausted = True
            meter_row.history_checked_until = window.start
            meter_row.updated_at = datetime.utcnow()
            session.commit()

    if not stored and errors:
        raise RuntimeError("Keine Verbrauchsdaten abrufbar: " + "; ".join(errors[:3]))

    return stored


def _known_start(session: Session, meter_id: str) -> datetime | None:
    """Der früheste bekannte Verbrauchswert."""
    return session.scalar(select(func.min(Interval.slot_start)).where(Interval.meter_id == meter_id))


def rebuild_intervals(session: Session, settings: Settings) -> int:
    """Recompute the 15 minute consumption slots from the raw readings."""
    since = datetime.utcnow() - timedelta(days=REBUILD_WINDOW_DAYS)
    readings = list(
        session.scalars(
            select(MeterReading)
            .where(MeterReading.read_at >= since)
            .order_by(MeterReading.meter_id, MeterReading.register_obis_code, MeterReading.read_at)
        )
    )
    if not readings:
        return 0

    grouped: dict[tuple[str, str], list[RawReading]] = {}
    for row in readings:
        key = (row.meter_id, row.register_obis_code or "")
        grouped.setdefault(key, []).append(
            RawReading(
                read_at=row.read_at,
                value=row.value,
                register_obis_code=row.register_obis_code or "",
                type_of_read=row.type_of_read,
                origin=row.origin,
                status=row.status,
            )
        )

    # Prefer the register with the most readings (the main consumption register).
    best_key = max(grouped, key=lambda key: len(grouped[key]))
    slots = derive_intervals(
        grouped[best_key],
        slot_minutes=settings.slot_minutes,
        unit=settings.energy_unit,
    )
    if not slots:
        return 0

    meter_id = best_key[0]
    values = [
        {
            "meter_id": meter_id,
            "slot_start": slot.slot_start,
            "slot_end": slot.slot_end,
            "kwh": slot.kwh,
            "source": "measured",
            "n_readings": slot.n_readings,
            "received_at": datetime.utcnow(),
        }
        for slot in slots
    ]
    statement = sqlite_insert(Interval).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["meter_id", "slot_start"],
        set_={
            "slot_end": statement.excluded.slot_end,
            "kwh": statement.excluded.kwh,
            "n_readings": statement.excluded.n_readings,
            "source": statement.excluded.source,
        },
    )
    session.execute(statement)
    session.commit()
    logger.info("Rebuilt {} consumption slots for meter {}", len(values), meter_id)
    return len(values)
