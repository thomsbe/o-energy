from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.client import KrakenClient
from oenergy.config import Settings
from oenergy.db.models import Agreement, Price
from oenergy.services.pricing import RateWindow, effective_at, group_rates
from oenergy.utils import parse_datetime, parse_decimal

FAR_PAST = datetime(2000, 1, 1)
FAR_FUTURE = datetime(2100, 1, 1)


def _to_ct_per_kwh(value: float | None, unit: str | None) -> float | None:
    """Normalise a price to cents per kWh."""
    if value is None:
        return None
    text = (unit or "").lower().replace(" ", "").replace("€", "eur")
    if "eur/mwh" in text:
        return value * 10.0
    if "eur/kwh" in text:
        return value * 100.0
    if "ct/kwh" in text:
        return value
    return value


def _to_ct_money(value: float | None) -> float | None:
    """Standing charges: the API may deliver cents or euros.

    Values below 100 are treated as euros - a standing charge of less than
    1 EUR per month does not exist.
    """
    if value is None:
        return None
    return value * 100.0 if value < 100 else value


def _store_price(
    session: Session,
    *,
    scope: str,
    source: str,
    rate_name: str,
    agreement_id: str | None,
    starts_at: datetime,
    ends_at: datetime,
    net_ct: float | None,
    gross_ct: float | None,
    unit: str | None,
    vat: float = 0.19,
    active_from: str | None = None,
    active_to: str | None = None,
) -> None:
    if starts_at is None:
        return
    if ends_at is None:
        ends_at = starts_at
    if gross_ct is None and net_ct is not None:
        gross_ct = net_ct * (1 + vat)

    row = session.scalar(
        select(Price).where(
            Price.scope == scope,
            Price.source == source,
            Price.starts_at == starts_at,
            Price.rate_name == rate_name,
        )
    )
    if row is None:
        row = Price(scope=scope, source=source, starts_at=starts_at, rate_name=rate_name)
        session.add(row)
    row.ends_at = ends_at
    row.agreement_id = agreement_id
    row.net_ct_per_kwh = net_ct
    row.gross_ct_per_kwh = gross_ct
    row.unit = unit
    row.active_from = active_from
    row.active_to = active_to
    row.updated_at = datetime.utcnow()


def sync_prices(
    session: Session,
    client: KrakenClient,
    settings: Settings,
    *,
    history_days: int = 0,
) -> int:
    """Fetch customer prices (and EPEX spot prices) and persist them.

    ``history_days`` holt die Börsenpreise für einen längeren Zeitraum nach -
    damit lassen sich auch die historischen Kundenpreise berechnen
    (Börsenpreis + Basiskomponente).
    """
    from oenergy.services.account import primary_agreement

    agreement = primary_agreement(session, settings)
    if agreement is None:
        logger.warning("No agreement found - cannot sync prices")
        return 0

    vat = float(agreement.vat_rate or 0.19)
    count = 0

    count += _sync_agreement_rates(session, client, agreement, vat, settings)
    count += _sync_agreement_forecast(session, client, agreement, vat)
    count += _sync_epex(session, client, settings, history_days=history_days)
    session.commit()
    return count


def _sync_agreement_rates(
    session: Session,
    client: KrakenClient,
    agreement: Agreement,
    vat: float,
    settings: Settings,
) -> int:
    """`agreementRates` returns the applicable rates incl. their validity period.

    A dynamic tariff consists of several rates per window (base component,
    variable component); every rate is stored separately and summed up later.
    """
    count = 0
    try:
        nodes = list(
            client.paginate(
                queries.AGREEMENT_RATES,
                {"agreementId": agreement.id},
                edges_path=("agreementRates",),
            )
        )
    except Exception as exc:  # API errors must not kill the sync
        # agreementRates is not available for customer accounts - the forecast
        # and the EPEX prices are used instead
        logger.info("agreementRates not available for {}: {}", agreement.id, exc)
        return 0

    for node in nodes:
        validity = node.get("validityPeriod") or {}
        starts_at = parse_datetime(validity.get("start"))
        ends_at = parse_datetime(validity.get("end"))
        if starts_at is None:
            continue
        price = parse_decimal(node.get("pricePerUnit"))
        ct = _to_ct_per_kwh(price, node.get("unit"))
        name = node.get("name") or node.get("category") or "rate"
        _store_price(
            session,
            scope="customer",
            source="agreement_rates",
            rate_name=str(name),
            agreement_id=agreement.id,
            starts_at=starts_at,
            ends_at=ends_at or starts_at + timedelta(minutes=settings.slot_minutes),
            net_ct=ct,
            gross_ct=None,
            unit=node.get("unit"),
            vat=vat,
        )
        count += 1

    logger.info("Stored {} agreement rates for {}", count, agreement.id)
    return count


def _sync_agreement_forecast(
    session: Session,
    client: KrakenClient,
    agreement: Agreement,
    vat: float,
) -> int:
    """`agreement(id).unitRateForecast` carries the upcoming customer prices."""
    payload = client.execute(queries.AGREEMENT_DETAILS, {"id": agreement.id})
    agreement_data = (payload.get("data") or {}).get("agreement") or {}

    product = agreement_data.get("product") or {}
    if product.get("isTimeOfUse") is not None:
        agreement.is_time_of_use = bool(product.get("isTimeOfUse"))
        if product.get("displayName"):
            agreement.product_name = product.get("displayName")
        if product.get("code"):
            agreement.product_code = product.get("code")

    standing = (agreement_data.get("standingChargeGrossRateInformation") or [{}])[0]
    if standing.get("grossRate") is not None:
        agreement.gross_monthly_standing_charge_ct = _to_ct_money(parse_decimal(standing.get("grossRate")))
        agreement.net_monthly_standing_charge_ct = _to_ct_money(
            parse_decimal(agreement_data.get("netMonthlyStandingCharge"))
        )
        reported_vat = parse_decimal(standing.get("vatRate"))
        if reported_vat is not None:
            # the API delivers the VAT in percent (19)
            agreement.vat_rate = reported_vat / 100 if reported_vat > 1 else reported_vat
        else:
            agreement.vat_rate = vat
        agreement.updated_at = datetime.utcnow()
        session.commit()

    count = 0
    for forecast in agreement_data.get("unitRateForecast") or []:
        valid_from = parse_datetime(forecast.get("validFrom"))
        valid_to = parse_datetime(forecast.get("validTo"))
        if valid_from is None:
            continue
        info = forecast.get("unitRateInformation") or {}
        for rate in info.get("rates") or []:
            _store_price(
                session,
                scope="customer",
                source="forecast",
                rate_name=str(rate.get("timeslotName") or "rate"),
                agreement_id=agreement.id,
                starts_at=valid_from,
                ends_at=valid_to or valid_from,
                net_ct=parse_decimal(rate.get("netUnitRateCentsPerKwh")),
                gross_ct=parse_decimal(rate.get("latestGrossUnitRateCentsPerKwh")),
                unit="ct/kWh",
                vat=vat,
            )
            count += 1

    count += _sync_static_rates(session, agreement_data, agreement, vat)
    logger.info("Stored {} forecast/static prices for {}", count, agreement.id)
    return count


def _sync_static_rates(
    session: Session,
    agreement_data: dict,
    agreement: Agreement,
    vat: float,
) -> int:
    """The product's fixed rates (base component) as fallback."""
    info = agreement_data.get("unitRateInformation") or {}
    rates = info.get("rates") or []
    if not rates:
        return 0

    starts_at = parse_datetime(agreement_data.get("validFrom")) or FAR_PAST
    ends_at = parse_datetime(agreement_data.get("validTo")) or FAR_FUTURE
    count = 0

    for rate in rates:
        active_from = None
        active_to = None
        rules = rate.get("timeslotActivationRules") or []
        if rules:
            rule = rules[0]
            active_from = rule.get("activeFromTime")
            active_to = rule.get("activeToTime")
        _store_price(
            session,
            scope="customer",
            source="static",
            rate_name=str(rate.get("timeslotName") or "base"),
            agreement_id=agreement.id,
            starts_at=starts_at,
            ends_at=ends_at,
            net_ct=parse_decimal(rate.get("netUnitRateCentsPerKwh")),
            gross_ct=parse_decimal(rate.get("latestGrossUnitRateCentsPerKwh")),
            unit="ct/kWh",
            vat=vat,
            active_from=active_from,
            active_to=active_to,
        )
        count += 1
    return count


def _sync_epex(
    session: Session,
    client: KrakenClient,
    settings: Settings,
    *,
    history_days: int = 0,
) -> int:
    """EPEX day ahead prices (reference for the dynamic tariff)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    start = now - timedelta(days=1)
    end = now + timedelta(hours=72)

    if history_days:
        wanted = now - timedelta(days=history_days)
        oldest = session.scalar(select(func.min(Price.starts_at)).where(Price.scope == "epex"))
        if oldest is None or oldest > wanted:
            start = wanted

    try:
        nodes = client.paginate(
            queries.EPEX_DAY_AHEAD,
            {"periodStart": start.isoformat() + "Z", "periodEnd": end.isoformat() + "Z"},
            edges_path=("epexDayAheadPrices",),
        )
    except Exception as exc:
        logger.warning("epexDayAheadPrices failed: {}", exc)
        return 0

    count = 0
    for node in nodes:
        starts_at = parse_datetime(node.get("periodStart"))
        ends_at = parse_datetime(node.get("periodEnd"))
        value = parse_decimal(node.get("value"))
        if starts_at is None or value is None:
            continue
        if value <= 0.001:
            # die API liefert für nicht vorhandene Slots -0.001
            continue
        # EPEX values are given in EUR/MWh -> ct/kWh is /10
        ct = value / 10.0
        _store_price(
            session,
            scope="epex",
            source="epex",
            rate_name="epex",
            agreement_id=None,
            starts_at=starts_at,
            ends_at=ends_at or starts_at + timedelta(minutes=15),
            net_ct=ct,
            gross_ct=None,
            unit=node.get("unit") or "EUR/MWh",
            vat=0.0,
        )
        count += 1

    logger.info("Stored {} EPEX prices", count)
    return count


def price_at(session: Session, at: datetime, *, scope: str = "customer") -> Price | None:
    """The price valid at a point in time (single rate)."""
    statement = (
        select(Price)
        .where(Price.scope == scope, Price.starts_at <= at, Price.ends_at > at)
        .order_by(Price.starts_at.desc())
    )
    return session.scalar(statement)


def epex_at(session: Session, at: datetime) -> Price | None:
    return price_at(session, at, scope="epex")


def price_series(
    session: Session,
    start: datetime,
    end: datetime,
    *,
    scope: str = "customer",
) -> list[Price]:
    statement = (
        select(Price)
        .where(Price.scope == scope, Price.starts_at >= start, Price.starts_at < end)
        .order_by(Price.starts_at)
    )
    return list(session.scalars(statement))


def effective_windows(
    session: Session,
    start: datetime,
    end: datetime,
    vat: float = 0.19,
) -> list[RateWindow]:
    """The effective customer price per window (all rates summed up)."""
    rows = price_series(session, start, end)
    return group_rates(rows, vat)


def effective_price_at(session: Session, at: datetime, vat: float = 0.19):
    """The price that applies at a point in time (sum of all valid rates)."""
    rows = list(session.scalars(select(Price).where(Price.starts_at <= at, Price.ends_at > at)))
    return effective_at(rows, at, vat=vat)
