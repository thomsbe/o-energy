from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Europe/Berlin")


def parse_datetime(value: Any) -> datetime | None:
    """Parse an API timestamp into a naive UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(tzinfo=None)


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = parse_datetime(text)
        return parsed.date() if parsed else None


def parse_decimal(value: Any) -> float | None:
    """Kraken returns decimals as float or string."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def to_local(value: datetime | None, tz: ZoneInfo = DEFAULT_TZ) -> datetime | None:
    """Convert a naive UTC datetime into an aware local datetime."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).astimezone(tz)


def local_day(value: datetime | None, tz: ZoneInfo = DEFAULT_TZ) -> date | None:
    local = to_local(value, tz)
    return local.date() if local else None


def floor_slot(value: datetime, slot_minutes: int) -> datetime:
    """Round a naive UTC datetime down to the start of its slot."""
    minutes = (value.minute // slot_minutes) * slot_minutes
    return value.replace(minute=minutes, second=0, microsecond=0)


def day_bounds_utc(day: date, tz: ZoneInfo = DEFAULT_TZ) -> tuple[datetime, datetime]:
    """UTC bounds of a local calendar day."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC).replace(tzinfo=None)
    end = (
        datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    return start, end


def days_in_month(day: date) -> int:
    next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (next_month - day.replace(day=1)).days
