"""Automatic backfill planning.

The utility only delivers a part of the history (and the most recent values
arrive with a delay of roughly 24 hours).  Instead of always asking for the
same range, the app keeps track of

* which time ranges are already covered by readings,
* which time ranges were already requested from the API.

Everything that is neither covered nor recently requested becomes a *gap* that
is requested from the API.  Already fetched ranges are never requested again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Interval, MeterReading, ReadingWindow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: Window) -> bool:
        return self.start < other.end and other.start < self.end

    def __bool__(self) -> bool:
        return self.end > self.start


def _local_midnight(day: datetime, tz: ZoneInfo) -> datetime:
    local = datetime.combine(day.date(), time.min, tzinfo=tz)
    return local.astimezone(UTC).replace(tzinfo=None)


def snap_to_days(start: datetime, end: datetime, tz: ZoneInfo) -> Window:
    """Snap a range to local midnight boundaries so that it stays stable."""
    start_local = start.replace(tzinfo=UTC).astimezone(tz)
    end_local = end.replace(tzinfo=UTC).astimezone(tz)
    snapped_start = _local_midnight(start_local, tz)
    snapped_end = max(_local_midnight(end_local, tz) + timedelta(days=1), end)
    return Window(snapped_start, snapped_end)


def subtract(target: Window, coverage: list[Window]) -> list[Window]:
    """Remove all covered parts from the target window."""
    gaps = [target]
    for cover in coverage:
        remaining: list[Window] = []
        for gap in gaps:
            if not cover.overlaps(gap):
                remaining.append(gap)
                continue
            if cover.start > gap.start:
                remaining.append(Window(gap.start, min(cover.start, gap.end)))
            if cover.end < gap.end:
                remaining.append(Window(max(cover.end, gap.start), gap.end))
        gaps = [gap for gap in remaining if gap]
    return gaps


def merge_windows(windows: list[Window]) -> list[Window]:
    if not windows:
        return []
    ordered = sorted(windows, key=lambda window: window.start)
    merged = [ordered[0]]
    for window in ordered[1:]:
        last = merged[-1]
        if window.start <= last.end:
            merged[-1] = Window(last.start, max(last.end, window.end))
        else:
            merged.append(window)
    return merged


def split_windows(windows: list[Window], max_days: int) -> list[Window]:
    """Split long ranges into chunks so that no query becomes too large."""
    chunks: list[Window] = []
    for window in windows:
        cursor = window.start
        while cursor < window.end:
            chunk_end = min(cursor + timedelta(days=max_days), window.end)
            chunks.append(Window(cursor, chunk_end))
            cursor = chunk_end
    return chunks


def data_windows(session: Session, meter_id: str) -> list[Window]:
    """Time ranges that are already covered by consumption intervals."""
    intervals = list(
        session.execute(
            select(Interval.slot_start, Interval.slot_end)
            .where(Interval.meter_id == meter_id)
            .order_by(Interval.slot_start)
        )
    )
    if intervals:
        return [Window(start, end) for start, end in intervals]

    # fallback: cumulative meter readings cover the interval that follows them
    timestamps = list(
        session.scalars(
            select(MeterReading.read_at)
            .where(MeterReading.meter_id == meter_id)
            .order_by(MeterReading.read_at)
        )
    )
    if not timestamps:
        return []
    if len(timestamps) == 1:
        return [Window(timestamps[0], timestamps[0] + timedelta(days=1))]

    deltas = [(current - previous).total_seconds() for previous, current in pairwise(timestamps)]
    deltas.sort()
    median_delta = deltas[len(deltas) // 2] or 900.0
    interval = timedelta(seconds=median_delta)

    return [Window(stamp, stamp + interval) for stamp in timestamps]


def queried_windows(session: Session, meter_id: str) -> list[Window]:
    return [
        Window(row.start_at, row.end_at)
        for row in session.scalars(select(ReadingWindow).where(ReadingWindow.meter_id == meter_id))
    ]


def has_data(session: Session, meter_id: str) -> bool:
    return (
        session.scalar(select(MeterReading.id).where(MeterReading.meter_id == meter_id).limit(1)) is not None
    )


def recently_queried(session: Session, meter_id: str, window: Window, minutes: int) -> bool:
    """True when the window was requested shortly ago."""
    threshold = datetime.utcnow() - timedelta(minutes=minutes)
    rows = session.scalars(
        select(ReadingWindow).where(
            ReadingWindow.meter_id == meter_id,
            ReadingWindow.queried_at >= threshold,
        )
    )
    return any(Window(row.start_at, row.end_at).overlaps(window) for row in rows)


def plan_queries(
    session: Session,
    meter_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> list[Window]:
    """Compute the time ranges that should be requested from the API."""
    now = now or datetime.utcnow()
    tz = ZoneInfo(settings.timezone)

    horizon_days = (
        settings.readings_history_days if not has_data(session, meter_id) else settings.readings_backfill_days
    )
    target = snap_to_days(now - timedelta(days=horizon_days), now, tz)

    coverage = merge_windows(data_windows(session, meter_id) + queried_windows(session, meter_id))
    gaps = subtract(target, coverage)

    # days that are not complete yet may still receive values - ask again
    from oenergy.db.models import DailyStat

    stats = {
        stat.day: stat
        for stat in session.scalars(
            select(DailyStat).where(DailyStat.meter_id == meter_id).order_by(DailyStat.day)
        )
    }
    local_today = now.replace(tzinfo=UTC).astimezone(tz).date()
    for offset in range(settings.readings_backfill_days + 1):
        day = local_today - timedelta(days=offset)
        stat = stats.get(day)
        if stat is not None and stat.is_final:
            continue
        day_start = _local_midnight(datetime.combine(day, time.min), tz)
        day_end = day_start + timedelta(days=1)
        gaps.append(Window(day_start, day_end))

    gaps = merge_windows([gap for gap in gaps if gap])
    chunks = split_windows(gaps, settings.readings_max_window_days)

    result = []
    for chunk in chunks:
        if recently_queried(session, meter_id, chunk, settings.gap_retry_minutes):
            continue
        result.append(chunk)
    return result


def record_query(session: Session, meter_id: str, window: Window, rows_found: int) -> None:
    row = session.scalar(
        select(ReadingWindow).where(
            ReadingWindow.meter_id == meter_id,
            ReadingWindow.start_at == window.start,
            ReadingWindow.end_at == window.end,
        )
    )
    if row is None:
        row = ReadingWindow(meter_id=meter_id, start_at=window.start, end_at=window.end)
        session.add(row)
    row.queried_at = datetime.utcnow()
    row.rows_found = rows_found
    row.status = "ok" if rows_found else "empty"
    session.commit()
