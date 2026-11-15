from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import (
    Account,
    Agreement,
    DailyStat,
    Interval,
    Meter,
    MeterReading,
    Price,
    Property,
)
from oenergy.services.account import active_meter_id
from oenergy.services.completeness import data_state, expected_delivery_at
from oenergy.services.pricing import PriceBook, compose_at, group_rates
from oenergy.utils import floor_slot, local_day, to_local


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
    yesterday = today - timedelta(days=1)
    today_cents = sum(stat.cost_cents for stat in stats if stat.day == today)
    yesterday_cents = sum(stat.cost_cents for stat in stats if stat.day == yesterday)
    month_cents = sum(stat.cost_cents for stat in stats if stat.day >= month_start)
    year_cents = sum(stat.cost_cents for stat in stats if stat.day >= year_start)

    month_kwh = sum(stat.kwh for stat in stats if stat.day >= month_start)
    days_in_month = _days_in_month(today)

    # --- Monatsprognose -------------------------------------------------
    # Die bereits angefallenen Kosten plus die noch erwarteten Tage.
    # Als Basis dienen die letzten Tage mit Daten (repräsentativer als der
    # Monatsschnitt, weil sich die Preise laufend ändern).
    month_stats = sorted(
        (stat for stat in stats if month_start <= stat.day < today),
        key=lambda stat: stat.day,
    )
    recent = month_stats[-FORECAST_BASIS_DAYS:]
    daily_costs = [stat.cost_cents for stat in recent]
    daily_avg = sum(daily_costs) / len(daily_costs) if daily_costs else 0.0
    daily_spread = 0.0
    if len(daily_costs) > 1:
        mean = daily_avg
        daily_spread = (sum((value - mean) ** 2 for value in daily_costs) / len(daily_costs)) ** 0.5

    # noch offene Tage (heute zählt mit - die Daten kommen erst morgen früh)
    remaining_days = max(0, days_in_month - len(month_stats))
    forecast_cents = month_cents + remaining_days * daily_avg
    # die Unsicherheit sinkt mit der Wurzel aus der Zahl der offenen Tage
    uncertainty_cents = daily_spread * (remaining_days**0.5) if remaining_days else 0.0

    return {
        "today_cents": today_cents,
        "yesterday_cents": yesterday_cents,
        "month_cents": month_cents,
        "year_cents": year_cents,
        "month_kwh": month_kwh,
        "avg_price_ct": (month_cents / month_kwh) if month_kwh else 0.0,
        "forecast_month_cents": forecast_cents,
        "forecast_uncertainty_cents": uncertainty_cents,
        "daily_avg_cents": daily_avg,
        "remaining_days": remaining_days,
        "basis_days": len(daily_costs),
        "days_in_month": days_in_month,
        "days_elapsed": len(month_stats),
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

    # Der heutige Tag hat noch keine Zählerdaten und gehört deshalb nicht
    # in das Diagramm (er kommt mit den Daten des Folgetags nach).
    days_meta = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        if day >= today:
            continue
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


def consumption_with_price(session: Session, settings: Settings, days: int = 2) -> dict:
    """Verbrauch und Preis je 15-Minuten-Slot.

    So wird sichtbar, zu welchem Preis verbraucht wurde.
    """
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    start = now - timedelta(days=days)
    meter_id = active_meter_id(session, settings)

    interval_query = select(Interval).where(Interval.slot_start >= start).order_by(Interval.slot_start)
    if meter_id is not None:
        interval_query = interval_query.where(Interval.meter_id == meter_id)
    consumption = {row.slot_start: row.kwh for row in session.scalars(interval_query)}

    book = PriceBook(
        session,
        start,
        now + timedelta(hours=1),
        vat=float(_vat_rate(session)),
        tz=tz,
    )

    labels: list[str] = []
    kwh: list[float | None] = []
    prices: list[float | None] = []
    cursor = floor_slot(start, settings.slot_minutes)
    while cursor <= now:
        price = book.at(cursor)
        labels.append(to_local(cursor, tz).isoformat())
        kwh.append(consumption.get(cursor))
        prices.append(price.gross_ct_per_kwh if price else None)
        cursor += timedelta(minutes=settings.slot_minutes)

    return {"labels": labels, "kwh": kwh, "price": prices}


def daily_consumption(session: Session, settings: Settings, year: int, month: int) -> list[dict]:
    """Tagesverbrauch eines Monats (für die Monatsansicht)."""
    meter_id = active_meter_id(session, settings)
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1)
    days_in_month = (last - first).days

    query = select(DailyStat).where(DailyStat.day >= first, DailyStat.day < last)
    if meter_id is not None:
        query = query.where(DailyStat.meter_id == meter_id)
    stats = {stat.day: stat for stat in session.scalars(query)}

    now = datetime.utcnow()
    result: list[dict] = []
    for offset in range(days_in_month):
        day = first + timedelta(days=offset)
        stat = stats.get(day)
        has_data = bool(stat and stat.received_slots)
        result.append(
            {
                "day": day.isoformat(),
                "label": day.strftime("%d.%m."),
                "kwh": stat.kwh if has_data else None,
                "cost_cents": stat.cost_cents if has_data else 0.0,
                "is_final": bool(stat.is_final) if stat else False,
                "state": data_state(day, stat, now, settings),
            }
        )
    return result


def cost_chart_data(session: Session, settings: Settings, days: int = 30) -> dict:
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    start = today - timedelta(days=days - 1)

    meter_id = active_meter_id(session, settings)
    stat_query = select(DailyStat).where(DailyStat.day >= start).order_by(DailyStat.day)
    if meter_id is not None:
        stat_query = stat_query.where(DailyStat.meter_id == meter_id)
    # Der heutige Tag hat noch keine Daten (Lieferung erst am Folgetag) -
    # er gehört nicht in das Diagramm.
    stats = [stat for stat in session.scalars(stat_query) if stat.day < today]
    return {
        "labels": [stat.day.isoformat() for stat in stats],
        "kwh": [stat.kwh for stat in stats],
        "cost": [stat.cost_cents / 100 for stat in stats],
        "energy_cost": [stat.energy_cost_cents / 100 for stat in stats],
        "standing_cost": [stat.standing_cost_cents / 100 for stat in stats],
        "is_final": [bool(stat.is_final) for stat in stats],
    }


#: Anzahl der Tage, die als Basis für die Monatsprognose dienen
FORECAST_BASIS_DAYS = 7

#: Vorgabe-Schwellen für die Einordnung (brutto, ct/kWh) - überschreibbar
#: über die Konfiguration (PRICE_CHEAP_LIMIT / PRICE_EXPENSIVE_LIMIT)
CHEAP_LIMIT = 26.0
EXPENSIVE_LIMIT = 40.0
#: Festpreisanteil (Basiskomponente) - die variable Komponente ist der Rest
FIXED_PART = 20.0


def _hue_anchors(cheap_limit: float, expensive_limit: float) -> tuple[tuple[float, float], ...]:
    """Ankerpunkte des Farbverlaufs, abhängig von den konfigurierten Schwellen."""
    middle = (cheap_limit + expensive_limit) / 2
    return (
        (0.0, 165.0),
        (cheap_limit, 105.0),
        (middle, 60.0),
        (expensive_limit, 35.0),
        (expensive_limit + 30.0, 0.0),
    )


def price_hue(
    value: float | None,
    cheap_limit: float = CHEAP_LIMIT,
    expensive_limit: float = EXPENSIVE_LIMIT,
) -> int:
    """Farbton für einen Preis: grün (günstig) -> gelb (normal) -> rot (teuer)."""
    if value is None:
        return 0
    anchors = _hue_anchors(cheap_limit, expensive_limit)
    if value <= anchors[0][0]:
        return int(anchors[0][1])
    if value >= anchors[-1][0]:
        return int(anchors[-1][1])
    for (low_price, low_hue), (high_price, high_hue) in pairwise(anchors):
        if low_price <= value <= high_price:
            span = high_price - low_price
            ratio = (value - low_price) / span if span else 0.0
            return round(low_hue + (high_hue - low_hue) * ratio)
    return int(anchors[-1][1])


def classify_price(
    value: float | None,
    cheap_limit: float = CHEAP_LIMIT,
    expensive_limit: float = EXPENSIVE_LIMIT,
) -> tuple[str, float, str]:
    """Preisklasse: (css-Klasse, Intensität 0..1, Beschriftung)."""
    if value is None:
        return "is-empty", 0.0, "keine Daten"
    if value < cheap_limit:
        level = min(1.0, (cheap_limit - value) / cheap_limit)
        return "is-cheap", level, "günstig"
    if value >= expensive_limit:
        level = min(1.0, (value - expensive_limit) / expensive_limit)
        return "is-expensive", level, "teuer"
    level = (value - cheap_limit) / (expensive_limit - cheap_limit)
    return "is-mid", level, "normal"


def heatmap_data(session: Session, settings: Settings, days: int = 7) -> dict:
    """Day x 15 minute slot price matrix.

    The prices are composed the same way as for the cost calculation: the
    applicable rates of the agreement or - for the past - the exchange price
    plus the fixed base component of the tariff.
    """
    tz = ZoneInfo(settings.timezone)
    now = datetime.utcnow()
    today = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
    start_day = today - timedelta(days=days - 1)
    vat = float(_vat_rate(session))
    cheap_limit = float(settings.price_cheap_limit)
    expensive_limit = float(settings.price_expensive_limit)

    window_start = (
        datetime.combine(start_day, datetime.min.time(), tzinfo=tz)
        .astimezone(ZoneInfo("UTC"))
        .replace(tzinfo=None)
    )
    book = PriceBook(
        session,
        window_start,
        now + timedelta(days=1),
        vat=vat,
        tz=tz,
    )

    slots_per_day = settings.slots_per_day
    matrix: list[list[dict | None]] = []
    labels: list[str] = []
    for offset in range(days):
        day = start_day + timedelta(days=offset)
        labels.append(day.isoformat())
        row: list[dict | None] = []
        for index in range(slots_per_day):
            local = datetime.combine(day, datetime.min.time(), tzinfo=tz) + timedelta(
                minutes=index * settings.slot_minutes
            )
            at = local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            price = book.at(at)
            if price is None or price.gross_ct_per_kwh is None:
                row.append(None)
                continue
            value = price.gross_ct_per_kwh
            css_class, level, label = classify_price(value, cheap_limit, expensive_limit)
            row.append(
                {
                    "value": value,
                    "net": price.net_ct_per_kwh,
                    "epex": round(value - FIXED_PART, 2),
                    "variable": round(value - FIXED_PART, 2),
                    "class": css_class,
                    "level": round(level, 3),
                    "hue": price_hue(value, cheap_limit, expensive_limit),
                    "label": label,
                }
            )
        matrix.append(row)

    return {
        "labels": labels,
        "matrix": matrix,
        "slot_minutes": settings.slot_minutes,
        "cheap_limit": cheap_limit,
        "expensive_limit": expensive_limit,
    }


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


def account_info(session: Session, settings: Settings) -> dict:
    """Konto, Verträge, Lieferpunkt und Zähler - wie von der API geliefert."""
    from oenergy.db.models import Token

    token = session.get(Token, 1)
    accounts = [
        {
            "number": row.number,
            "brand": row.brand,
            "status": row.status,
            "billing_name": row.billing_name,
            "billing_email": row.billing_email,
            "balance_cents": row.balance_cents,
            "overdue_cents": row.overdue_cents,
        }
        for row in session.scalars(select(Account).order_by(Account.number))
    ]
    agreements = [
        {
            "id": row.id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "is_time_of_use": row.is_time_of_use,
            "is_active": row.is_active,
            "supply_point": row.supply_point_external_id,
            "market": row.supply_point_market,
            "valid_from": to_local(row.valid_from, ZoneInfo(settings.timezone)),
            "standing_charge_ct": row.gross_monthly_standing_charge_ct,
            "vat_rate": row.vat_rate,
        }
        for row in session.scalars(select(Agreement))
    ]
    properties = [
        {
            "id": row.id,
            "address": row.address,
            "malo_number": row.malo_number,
            "meter_number": row.meter_number,
            "melo_number": row.melo_number,
            "has_smart_meter_gateway": row.has_smart_meter_gateway,
        }
        for row in session.scalars(select(Property))
    ]
    meters = [
        {
            "id": row.id,
            "number": row.number,
            "melo_number": row.melo_number,
            "has_smart_meter_gateway": row.has_smart_meter_gateway,
            "active_to": to_local(row.active_to, ZoneInfo(settings.timezone)),
            "history_start": to_local(row.history_start, ZoneInfo(settings.timezone)),
            "history_exhausted": row.history_exhausted,
            "history_checked_until": to_local(row.history_checked_until, ZoneInfo(settings.timezone)),
        }
        for row in session.scalars(select(Meter))
    ]
    return {
        "email": token.viewer_email if token else None,
        "name": token.viewer_name if token else None,
        "accounts": accounts,
        "agreements": agreements,
        "properties": properties,
        "meters": meters,
    }


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


def interval_rows(session: Session, settings: Settings, limit: int = 60) -> list[dict]:
    """Die zuletzt gemessenen Verbrauchsintervalle."""
    tz = ZoneInfo(settings.timezone)
    meter_id = active_meter_id(session, settings)
    query = select(Interval).order_by(Interval.slot_start.desc()).limit(limit)
    if meter_id is not None:
        query = query.where(Interval.meter_id == meter_id)
    rows = list(session.scalars(query))
    return [
        {
            "meter_id": row.meter_id,
            "start": to_local(row.slot_start, tz),
            "end": to_local(row.slot_end, tz),
            "kwh": row.kwh,
            "source": row.source,
        }
        for row in reversed(rows)
    ]


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
