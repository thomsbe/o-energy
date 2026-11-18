"""Read-only, aggregated data for the MCP interface.

The functions in this module are plain Python: they know neither FastAPI nor
MCP.  They receive a :class:`~sqlalchemy.orm.Session` (always a read-only one
from the MCP server), aggregate the stored data and return JSON-serialisable
dictionaries.

Privacy: the answers intentionally contain no identifiers (account number,
meter/MaLo number, e-mail address or name).  Times are local ISO-8601 strings,
prices are in ct/kWh and money is in EUR.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Account, DailyStat, Interval, Price, SyncRun
from oenergy.services import completeness, dashboard
from oenergy.services.account import active_meter_id, primary_agreement, primary_meter
from oenergy.services.pricing import EffectivePrice, RateWindow, compose_at, effective_at, group_rates
from oenergy.utils import to_local

DEFAULT_VAT = 19.0
MAX_DAYS = 366
MAX_HOURS = 168
MAX_WINDOWS = 500


def _tz(settings: Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _now_local(settings: Settings) -> datetime:
    return to_local(datetime.utcnow(), _tz(settings))  # type: ignore[return-value]


def _iso(value: datetime | None, tz: ZoneInfo) -> str | None:
    """A local ISO-8601 timestamp with offset - never UTC in the answer."""
    if value is None:
        return None
    local = to_local(value, tz) if value.tzinfo is None else value.astimezone(tz)
    return local.replace(microsecond=0).isoformat()


def _date_iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _number(value: float | int | None, digits: int = 3) -> float | None:
    return round(float(value), digits) if value is not None else None


def _euros(cents: float | int | None) -> float | None:
    return round(float(cents) / 100, 2) if cents is not None else None


def _vat_rate(session: Session, settings: Settings) -> float:
    agreement = primary_agreement(session, settings)
    value = agreement.vat_rate if agreement is not None and agreement.vat_rate else DEFAULT_VAT
    return float(value) * 100 if float(value) < 1 else float(value)


def _price_payload(price: EffectivePrice | None, tz: ZoneInfo) -> dict | None:
    if price is None:
        return None
    return {
        "net_ct_per_kwh": _number(price.net_ct, 2),
        "gross_ct_per_kwh": _number(price.gross_ct, 2),
        "source": price.source,
        "components": list(price.rate_names),
        "valid_from": _iso(price.starts_at, tz),
        "valid_to": _iso(price.ends_at, tz),
    }


def _window_dict(window: RateWindow, tz: ZoneInfo) -> dict:
    return {
        "starts_at": _iso(window.starts_at, tz),
        "ends_at": _iso(window.ends_at, tz),
        "net_ct_per_kwh": _number(window.net_ct, 2),
        "gross_ct_per_kwh": _number(window.gross_ct, 2),
        "components": list(window.rate_names),
    }


def _daily_stats(
    session: Session, settings: Settings, meter_id: str | None, first_day: date, last_day: date
) -> dict[date, DailyStat]:
    query = select(DailyStat).where(DailyStat.day >= first_day, DailyStat.day <= last_day)
    if meter_id is not None:
        query = query.where(DailyStat.meter_id == meter_id)
    return {stat.day: stat for stat in session.scalars(query)}


def _day_rows(
    session: Session,
    settings: Settings,
    *,
    days: int,
    with_cost: bool,
) -> tuple[list[dict], dict]:
    """The last ``days`` complete local days (today never contains readings)."""
    now = datetime.utcnow()
    today = _now_local(settings).date()
    first_day = today - timedelta(days=days)
    last_day = today - timedelta(days=1)
    meter_id = active_meter_id(session, settings)
    stats = _daily_stats(session, settings, meter_id, first_day, last_day)

    rows: list[dict] = []
    total_kwh = 0.0
    total_cost = 0.0
    total_energy = 0.0
    total_standing = 0.0
    days_with_data = 0
    provisional_days = 0

    for offset in range(days, 0, -1):
        day = today - timedelta(days=offset)
        stat = stats.get(day)
        has_data = bool(stat and stat.received_slots)
        state = completeness.data_state(day, stat, now, settings)
        if has_data:
            days_with_data += 1
            total_kwh += stat.kwh or 0.0
            if with_cost:
                total_cost += stat.cost_cents or 0
                total_energy += stat.energy_cost_cents or 0
                total_standing += stat.standing_cost_cents or 0
        if has_data and not (stat and stat.is_final):
            provisional_days += 1
        row = {
            "day": day.isoformat(),
            "kwh": _number(stat.kwh, 3) if has_data else None,
            "state": state,
            "is_final": bool(stat.is_final) if stat else False,
        }
        if with_cost:
            row["cost_eur"] = _euros(stat.cost_cents) if has_data else None
            row["energy_cost_eur"] = _euros(stat.energy_cost_cents) if has_data else None
            row["standing_cost_eur"] = _euros(stat.standing_cost_cents) if has_data else None
        rows.append(row)

    summary = {
        "days": days,
        "from": first_day.isoformat(),
        "to": last_day.isoformat(),
        "total_kwh": _number(total_kwh, 3),
        "days_with_data": days_with_data,
        "provisional_days": provisional_days,
    }
    if with_cost:
        summary.update(
            {
                "total_cost_eur": _euros(total_cost),
                "energy_cost_eur": _euros(total_energy),
                "standing_cost_eur": _euros(total_standing),
            }
        )
    return rows, summary


def get_status(session: Session, settings: Settings) -> dict:
    """Data freshness, completeness and the last sync runs."""
    tz = _tz(settings)
    runs = list(session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(100)))
    latest: dict[str, SyncRun] = {}
    for run in runs:
        latest.setdefault(run.job, run)
    last_success = next((run for run in runs if run.status == "ok"), None)

    def run_dict(run: SyncRun | None) -> dict | None:
        if run is None:
            return None
        return {
            "job": run.job,
            "status": run.status,
            "started_at": _iso(run.started_at, tz),
            "finished_at": _iso(run.finished_at, tz),
            "items": run.items,
        }

    days = []
    for entry in completeness.completeness_summary(session, settings, days=7):
        days.append(
            {
                "day": _date_iso(entry["day"]),
                "state": entry["state"],
                "kwh": _number(entry["kwh"], 3) if entry["received_slots"] else None,
                "cost_eur": _euros(entry["cost_cents"]) if entry["received_slots"] else None,
                "received_slots": entry["received_slots"],
                "expected_slots": entry["expected_slots"],
                "is_final": entry["is_final"],
                "finalized_at": _iso(entry["finalized_at"], tz),
                "delivery_expected_at": _iso(entry["delivery_expected_at"], tz),
            }
        )

    latest_end = session.scalar(select(func.max(Interval.slot_end)))
    latest_received = session.scalar(select(func.max(Interval.received_at)))
    interval_count = session.scalar(select(func.count()).select_from(Interval)) or 0

    return {
        "generated_at": _now_local(settings).replace(microsecond=0).isoformat(),
        "data": {
            "interval_count": int(interval_count),
            "latest_interval_end": _iso(latest_end, tz),
            "latest_interval_received_at": _iso(latest_received, tz),
            "complete_days_last_7": sum(1 for entry in days if entry["is_final"]),
            "provisional_days_last_7": sum(1 for entry in days if entry["kwh"] and not entry["is_final"]),
        },
        "jobs": [run_dict(latest[job]) for job in sorted(latest)],
        "last_success": run_dict(last_success),
        "days": days,
    }


def get_consumption(session: Session, settings: Settings, days: int = 7) -> dict:
    """Consumption per complete day (kWh) with its completeness state."""
    days = max(1, min(int(days), MAX_DAYS))
    rows, summary = _day_rows(session, settings, days=days, with_cost=False)
    summary["daily"] = rows
    return summary


def get_costs(session: Session, settings: Settings, days: int = 30) -> dict:
    """Costs per complete day plus the current month forecast."""
    days = max(1, min(int(days), MAX_DAYS))
    rows, summary = _day_rows(session, settings, days=days, with_cost=True)
    month = dashboard.cost_summary(session, settings)
    summary.update(
        {
            "currency": "EUR",
            "daily": rows,
            "month": {
                "kwh": _number(month["month_kwh"], 3),
                "cost_eur": _euros(month["month_cents"]),
                "avg_price_ct_per_kwh": _number(month["avg_price_ct"], 2),
                "forecast_eur": _euros(month["forecast_month_cents"]),
                "forecast_uncertainty_eur": _euros(month["forecast_uncertainty_cents"]),
                "remaining_days": month["remaining_days"],
                "days_elapsed": month["days_elapsed"],
            },
        }
    )
    return summary


def get_prices(session: Session, settings: Settings, hours: int = 48) -> dict:
    """Current and upcoming customer prices (gross and net) plus EPEX."""
    hours = max(1, min(int(hours), MAX_HOURS))
    tz = _tz(settings)
    vat = _vat_rate(session, settings)
    now = datetime.utcnow()
    end = now + timedelta(hours=hours)

    rows = list(
        session.scalars(
            select(Price).where(Price.starts_at < end, Price.ends_at > now).order_by(Price.starts_at)
        )
    )
    customer_rows = [row for row in rows if row.scope != "epex"]
    epex_rows = [row for row in rows if row.scope == "epex"]
    current = compose_at(customer_rows, epex_rows, now, vat=vat, tz=tz)
    windows = group_rates(customer_rows, vat)
    epex_now = next((row for row in epex_rows if row.starts_at <= now < row.ends_at), None)

    return {
        "generated_at": _now_local(settings).replace(microsecond=0).isoformat(),
        "hours": hours,
        "slot_minutes": settings.slot_minutes,
        "current": _price_payload(current, tz),
        "epex_ct_per_kwh": _number(epex_now.net_ct_per_kwh, 2) if epex_now else None,
        "windows": [_window_dict(window, tz) for window in windows[:MAX_WINDOWS]],
        "truncated": len(windows) > MAX_WINDOWS,
    }


def get_cheapest_windows(session: Session, settings: Settings, days: int = 7, count: int = 10) -> dict:
    """The cheapest upcoming slots - useful for planning flexible loads."""
    days = max(1, min(int(days), 30))
    count = max(1, min(int(count), 50))
    tz = _tz(settings)
    vat = _vat_rate(session, settings)
    now = datetime.utcnow()
    end = now + timedelta(days=days)

    customer_rows = list(
        session.scalars(
            select(Price)
            .where(Price.scope != "epex", Price.starts_at < end, Price.ends_at > now)
            .order_by(Price.starts_at)
        )
    )
    windows = [window for window in group_rates(customer_rows, vat) if window.ends_at > now]
    cheapest = sorted(windows, key=lambda window: window.gross_ct)[:count]
    available_until = max((window.ends_at for window in windows), default=None)

    return {
        "generated_at": _now_local(settings).replace(microsecond=0).isoformat(),
        "days": days,
        "count": count,
        "available_until": _iso(available_until, tz),
        "windows": [_window_dict(window, tz) for window in cheapest],
    }


def get_price_at(session: Session, settings: Settings, when: str | datetime) -> dict:
    """The customer price at a specific local time."""
    tz = _tz(settings)
    at = _parse_when(when, tz)
    if at is None:
        raise ValueError("when must be an ISO-8601 date or timestamp")
    vat = _vat_rate(session, settings)

    rows = list(
        session.scalars(
            select(Price).where(Price.starts_at <= at, Price.ends_at > at).order_by(Price.starts_at)
        )
    )
    price = effective_at(rows, at, vat=vat, tz=tz)
    epex = next((row for row in rows if row.scope == "epex"), None)

    return {
        "when": _iso(at, tz),
        "found": price is not None,
        "price": _price_payload(price, tz),
        "epex_ct_per_kwh": _number(epex.net_ct_per_kwh, 2) if epex else None,
    }


def get_monthly_overview(session: Session, settings: Settings, month: str | None = None) -> dict:
    """Consumption, costs and average price for one month (YYYY-MM)."""
    today = _now_local(settings).date()
    if month:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise ValueError("month must use the format YYYY-MM") from exc
        year, month_number = parsed.year, parsed.month
    else:
        year, month_number = today.year, today.month

    rows = dashboard.daily_consumption(session, settings, year, month_number)
    total_kwh = sum(row["kwh"] or 0.0 for row in rows)
    total_cents = sum(row["cost_cents"] or 0.0 for row in rows)
    days_with_data = sum(1 for row in rows if row["kwh"] is not None)
    days_final = sum(1 for row in rows if row["is_final"])

    return {
        "month": f"{year:04d}-{month_number:02d}",
        "kwh": _number(total_kwh, 3),
        "cost_eur": _euros(total_cents),
        "avg_price_ct_per_kwh": _number(total_cents / total_kwh, 2) if total_kwh else None,
        "days_with_data": days_with_data,
        "days_final": days_final,
        "month_complete": days_final == len(rows),
        "daily": [
            {
                "day": row["day"],
                "kwh": _number(row["kwh"], 3),
                "cost_eur": _euros(row["cost_cents"]) if row["kwh"] is not None else None,
                "state": row["state"],
                "is_final": row["is_final"],
            }
            for row in rows
        ],
    }


def get_account(session: Session, settings: Settings) -> dict:
    """Tariff, base fee, VAT and balance - without any identifier."""
    agreement = primary_agreement(session, settings)
    meter = primary_meter(session, settings)
    balances = list(session.scalars(select(Account.balance_cents)))
    overdue = list(session.scalars(select(Account.overdue_cents)))
    vat = _vat_rate(session, settings)

    return {
        "tariff": {
            "name": (agreement.product_name or agreement.product_code) if agreement else None,
            "is_time_of_use": bool(agreement.is_time_of_use) if agreement else None,
            "valid_from": _iso(agreement.valid_from, _tz(settings)) if agreement else None,
            "valid_until": _iso(agreement.valid_to, _tz(settings)) if agreement else None,
        },
        "pricing": {
            "monthly_base_fee_eur": _euros(agreement.gross_monthly_standing_charge_ct) if agreement else None,
            "vat_percent": _number(vat, 2),
        },
        "balance_eur": _euros(sum(value or 0 for value in balances)) if balances else None,
        "overdue_eur": _euros(sum(value or 0 for value in overdue)) if overdue else None,
        "metering": {
            "smart_meter_gateway": bool(meter.has_smart_meter_gateway) if meter else None,
        },
        "data_available": agreement is not None,
    }


def _parse_when(value: str | datetime, tz: ZoneInfo) -> datetime | None:
    """Parse an ISO timestamp; a value without offset is local time."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC).replace(tzinfo=None)
