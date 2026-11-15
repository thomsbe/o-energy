from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Agreement, DailyStat, Interval, MeterReading, Price
from oenergy.services.account import active_meter_id
from oenergy.services.completeness import data_state, expected_delivery_at
from oenergy.services.pricing import compose_at, group_rates
from oenergy.utils import local_day, to_local

logger = logging.getLogger(__name__)


def _fmt_price(ct_per_kwh: float | None) -> str:
    """Prices are stored in cents per kWh (the German convention)."""
    if ct_per_kwh is None:
        return "-"
    return f"{ct_per_kwh:.2f} ct/kWh"


def price_overview(session: Session, settings: Settings) -> dict:
    """Current and upcoming prices (all rates of a window summed up)."""
    now = datetime.utcnow()
    tz = ZoneInfo(settings.timezone)
    vat = float(_vat_rate(session))

    rows = list(
        session.scalars(
            select(Price).where(Price.starts_at <= now + timedelta(hours=48), Price.ends_at > now)
        )
    )
    customer_rows = [row for row in rows if row.scope != "epex"]
    epex_rows = [row for row in rows if row.scope == "epex"]

    current = compose_at(customer_rows, epex_rows, now, vat=vat, tz=tz)
    windows = group_rates(customer_rows, vat)
    upcoming = [window for window in windows if window.starts_at > now][:48]

    next_window = upcoming[0] if upcoming else None
    epex = next((row for row in epex_rows if row.starts_at <= now < row.ends_at), None)

    return {
        "current": {
            "net_ct": current.net_ct if current else None,
            "gross_ct": current.gross_ct if current else None,
            "label": _fmt_price(current.gross_ct if current else None),
            "valid_from": to_local(current.starts_at, tz) if current and current.starts_at else None,
            "valid_to": to_local(current.ends_at, tz) if current and current.ends_at else None,
            "source": current.source if current else None,
            "components": current.rate_names if current else [],
            "rates": [
                {
                    "name": rate.name,
                    "net_ct": rate.net_ct,
                    "gross_ct": rate.gross_ct,
                }
                for rate in (current.rates if current else [])
            ],
        },
        "next": {
            "net_ct": next_window.net_ct if next_window else None,
            "gross_ct": next_window.gross_ct if next_window else None,
            "label": _fmt_price(next_window.gross_ct if next_window else None),
            "valid_from": to_local(next_window.starts_at, tz) if next_window else None,
        },
        "epex_ct": epex.net_ct_per_kwh if epex else None,
        "upcoming": [
            {
                "starts_at": to_local(window.starts_at, tz),
                "ends_at": to_local(window.ends_at, tz),
                "net_ct": window.net_ct,
                "gross_ct": window.gross_ct,
                "components": window.rate_names,
            }
            for window in upcoming
        ],
    }


def _vat_rate(session: Session) -> float:
    agreement = session.scalar(select(Agreement).order_by(Agreement.updated_at.desc()))
    return float(agreement.vat_rate or 0.19) if agreement else 0.19


def agreement_info(session: Session, settings: Settings) -> dict | None:
    agreement = session.scalar(select(Agreement).order_by(Agreement.updated_at.desc()))
    if agreement is None:
        return None
    return {
        "id": agreement.id,
        "product": agreement.product_name,
        "product_code": agreement.product_code,
        "is_time_of_use": agreement.is_time_of_use,
        "standing_charge_ct": agreement.gross_monthly_standing_charge_ct,
        "vat_rate": agreement.vat_rate,
        "account_number": agreement.account_number,
        "melo_number": agreement.melo_number,
        "supply_point": agreement.supply_point_external_id,
    }


def consumption_summary(session: Session, settings: Settings) -> dict:
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    yesterday = today - timedelta(days=1)

    meter_id = active_meter_id(session, settings)
    stats = {
        stat.day: stat
        for stat in session.scalars(select(DailyStat))
        if meter_id is None or stat.meter_id == meter_id
    }

    def kwh_for(day: date) -> float:
        stat = stats.get(day)
        return stat.kwh if stat else 0.0

    last_7 = sum(kwh_for(today - timedelta(days=offset)) for offset in range(1, 8))
    last_30 = sum(kwh_for(today - timedelta(days=offset)) for offset in range(1, 31))

    today_stat = stats.get(today)
    yesterday_stat = stats.get(yesterday)
    return {
        "today_kwh": kwh_for(today),
        "yesterday_kwh": kwh_for(yesterday),
        "last_7_kwh": last_7,
        "last_30_kwh": last_30,
        "avg_daily_kwh": last_7 / 7 if last_7 else 0.0,
        "today_state": data_state(today, today_stat, now, settings),
        "yesterday_state": data_state(yesterday, yesterday_stat, now, settings),
        "yesterday_final": bool(yesterday_stat.is_final) if yesterday_stat else False,
        "yesterday_delivery": expected_delivery_at(yesterday, settings),
    }


def cost_summary(session: Session, settings: Settings) -> dict:
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    meter_id = active_meter_id(session, settings)
    stats = [
        stat for stat in session.scalars(select(DailyStat)) if meter_id is None or stat.meter_id == meter_id
    ]
    stats_by_day = {stat.day: stat for stat in stats}
    today_cents = sum(stat.cost_cents for stat in stats if stat.day == today)
    month_cents = sum(stat.cost_cents for stat in stats if stat.day >= month_start)
    year_cents = sum(stat.cost_cents for stat in stats if stat.day >= year_start)

    month_kwh = sum(stat.kwh for stat in stats if stat.day >= month_start)
    month_days = (today - month_start).days + 1
    days_in_month = _days_in_month(today)
    forecast_cents = 0.0
    daily_avg = (month_cents / month_days) if month_days else 0.0
    for offset in range(month_days, days_in_month + 1):
        stat = stats_by_day.get(today.replace(day=offset))
        forecast_cents += stat.cost_cents if stat else daily_avg

    return {
        "today_cents": today_cents,
        "month_cents": month_cents,
        "year_cents": year_cents,
        "month_kwh": month_kwh,
        "avg_price_ct": (month_cents / month_kwh) if month_kwh else 0.0,
        "forecast_month_cents": forecast_cents,
        "days_in_month": days_in_month,
        "days_elapsed": month_days,
    }


def _days_in_month(day: date) -> int:
    next_month = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (next_month - day.replace(day=1)).days


def price_chart_data(session: Session, settings: Settings, hours: int = 48) -> dict:
    """Customer prices (summed per window) and EPEX prices for the charts."""
    tz = ZoneInfo(settings.timezone)
    vat = float(_vat_rate(session))
    now = datetime.utcnow()
    start = now - timedelta(hours=6)
    end = now + timedelta(hours=hours)

    rows = list(
        session.scalars(
            select(Price).where(Price.starts_at >= start, Price.starts_at < end).order_by(Price.starts_at)
        )
    )
    customer_rows = [row for row in rows if row.scope != "epex"]
    epex_rows = [row for row in rows if row.scope == "epex"]

    windows = group_rates(customer_rows, vat)
    epex_by_start = {row.starts_at: row.net_ct_per_kwh for row in epex_rows}

    customer = [
        {
            "gross": window.gross_ct,
            "net": window.net_ct,
            "start": to_local(window.starts_at, tz).isoformat(),
            "end": to_local(window.ends_at, tz).isoformat(),
            "components": window.rate_names,
        }
        for window in windows
    ]
    epex = [
        {"net": row.net_ct_per_kwh, "start": to_local(row.starts_at, tz).isoformat()} for row in epex_rows
    ]

    return {
        "labels": [entry["start"] for entry in customer],
        "customer": customer,
        "epex": epex,
        "epex_by_start": {to_local(key, tz).isoformat(): value for key, value in epex_by_start.items()},
    }


def consumption_chart_data(session: Session, settings: Settings, days: int = 3) -> dict:
    """Consumption per slot for the last days (marked as provisional if incomplete)."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    start_day = today - timedelta(days=days - 1)

    meter_id = active_meter_id(session, settings)
    interval_query = select(Interval).order_by(Interval.slot_start)
    stat_query = select(DailyStat)
    if meter_id is not None:
        interval_query = interval_query.where(Interval.meter_id == meter_id)
        stat_query = stat_query.where(DailyStat.meter_id == meter_id)
    intervals = list(session.scalars(interval_query))
    stats = {stat.day: stat for stat in session.scalars(stat_query)}

    series: dict[str, list[dict]] = {}
    for interval in intervals:
        day = local_day(interval.slot_start, tz)
        if day is None or day < start_day:
            continue
        key = day.isoformat()
        series.setdefault(key, []).append(
            {
                "t": to_local(interval.slot_start, tz).isoformat(),
                "kwh": interval.kwh,
                "cost": interval.kwh * 0.0,
            }
        )

    days_meta = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        stat = stats.get(day)
        days_meta.append(
            {
                "day": day.isoformat(),
                "state": data_state(day, stat, now, settings),
                "is_final": bool(stat.is_final) if stat else False,
                "kwh": stat.kwh if stat else 0.0,
            }
        )

    return {"days": days_meta, "series": series}


def cost_chart_data(session: Session, settings: Settings, days: int = 30) -> dict:
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    start = today - timedelta(days=days - 1)

    meter_id = active_meter_id(session, settings)
    stat_query = select(DailyStat).where(DailyStat.day >= start).order_by(DailyStat.day)
    if meter_id is not None:
        stat_query = stat_query.where(DailyStat.meter_id == meter_id)
    stats = list(session.scalars(stat_query))
    return {
        "labels": [stat.day.isoformat() for stat in stats],
        "kwh": [stat.kwh for stat in stats],
        "cost": [stat.cost_cents / 100 for stat in stats],
        "energy_cost": [stat.energy_cost_cents / 100 for stat in stats],
        "standing_cost": [stat.standing_cost_cents / 100 for stat in stats],
        "is_final": [bool(stat.is_final) for stat in stats],
    }


def heatmap_data(session: Session, settings: Settings, days: int = 7) -> dict:
    """Day x 15 minute slot price matrix."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    start_day = today - timedelta(days=days - 1)

    prices = list(
        session.scalars(
            select(Price)
            .where(
                Price.scope == "customer",
                Price.starts_at >= start_day - timedelta(days=1),
            )
            .order_by(Price.starts_at)
        )
    )
    if not prices:
        prices = list(session.scalars(select(Price).where(Price.scope == "epex").order_by(Price.starts_at)))

    slots_per_day = settings.slots_per_day
    matrix: list[list[float | None]] = []
    labels: list[str] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        labels.append(day.isoformat())
        row: list[float | None] = [None] * slots_per_day
        for price in prices:
            local = to_local(price.starts_at, tz)
            if local is None or local.date() != day:
                continue
            index = min(slots_per_day - 1, (local.hour * 60 + local.minute) // settings.slot_minutes)
            row[index] = price.gross_ct_per_kwh or price.net_ct_per_kwh
        matrix.append(row)

    return {"labels": labels, "matrix": matrix, "slot_minutes": settings.slot_minutes}


def cheapest_windows(session: Session, settings: Settings, hours: int = 24, count: int = 5) -> list[dict]:
    """The cheapest upcoming slots - handy for planning loads."""
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    end = now + timedelta(hours=hours)

    customer = list(
        session.scalars(
            select(Price)
            .where(Price.scope == "customer", Price.starts_at >= now, Price.starts_at < end)
            .order_by(Price.starts_at)
        )
    )
    epex = {
        row.starts_at: row.net_ct_per_kwh
        for row in session.scalars(
            select(Price).where(Price.scope == "epex", Price.starts_at >= now, Price.starts_at < end)
        )
    }

    slots = []
    for row in customer:
        gross = row.gross_ct_per_kwh or row.net_ct_per_kwh
        if gross is None:
            continue
        slots.append(
            {
                "start": to_local(row.starts_at, tz),
                "end": to_local(row.ends_at, tz),
                "gross": gross,
                "net": row.net_ct_per_kwh,
                "epex": epex.get(row.starts_at),
            }
        )
    slots.sort(key=lambda item: item["gross"])
    return slots[:count]


def backfill_overview(session: Session, settings: Settings) -> dict:
    """State of the automatic backfill: fetched windows and open gaps."""
    from oenergy.db.models import ReadingWindow
    from oenergy.services.backfill import plan_queries

    tz = ZoneInfo(settings.timezone)
    windows = list(session.scalars(select(ReadingWindow).order_by(ReadingWindow.start_at.desc()).limit(50)))
    meter_ids = list(session.scalars(select(Interval.meter_id).distinct()))
    pending: list[dict] = []
    for meter_id in meter_ids:
        for window in plan_queries(session, meter_id, settings):
            pending.append(
                {
                    "meter_id": meter_id,
                    "start": to_local(window.start, tz),
                    "end": to_local(window.end, tz),
                }
            )
    return {
        "windows": [
            {
                "meter_id": row.meter_id,
                "start": to_local(row.start_at, tz),
                "end": to_local(row.end_at, tz),
                "queried_at": to_local(row.queried_at, tz),
                "rows_found": row.rows_found,
                "status": row.status,
            }
            for row in windows
        ],
        "pending": pending,
    }


def meter_readings(session: Session, limit: int = 50) -> list[dict]:
    tz = settings_tz()
    rows = list(session.scalars(select(MeterReading).order_by(MeterReading.read_at.desc()).limit(limit)))
    return [
        {
            "meter_id": row.meter_id,
            "read_at": to_local(row.read_at, tz),
            "value": row.value,
            "obis": row.register_obis_code,
            "type_of_read": row.type_of_read,
            "origin": row.origin,
            "status": row.status,
            "received_at": to_local(row.received_at, tz),
        }
        for row in reversed(rows)
    ]


def settings_tz() -> ZoneInfo:
    from oenergy.config import get_settings

    return ZoneInfo(get_settings().timezone)
