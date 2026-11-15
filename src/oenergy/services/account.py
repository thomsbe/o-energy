from __future__ import annotations

import json
from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.client import KrakenClient
from oenergy.api.errors import KrakenError
from oenergy.config import Settings
from oenergy.db.models import Account, Agreement, Meter, Property, Token
from oenergy.utils import parse_datetime

ELECTRICITY_HINTS = ("electric", "strom", "power")
TIME_OF_USE_HINTS = ("dynamic", "tou", "zeitzone")


def sync_account(session: Session, client: KrakenClient, settings: Settings) -> Account | None:
    """Read the customer account, its agreements and the electricity meters."""
    payload = client.execute(queries.VIEWER_ACCOUNTS)
    viewer = (payload.get("data") or {}).get("viewer") or {}
    accounts = viewer.get("accounts") or []
    if not accounts:
        logger.warning("No accounts returned for user {}", viewer.get("email"))
        return None

    _store_identity(session, viewer)

    wanted = settings.account_number
    account_row: Account | None = None
    for raw_account in accounts:
        number = raw_account.get("number")
        if not number:
            continue
        if wanted and number != wanted:
            continue
        account_row = _upsert_account(session, raw_account)
        _sync_agreements(session, raw_account)
        break

    if account_row is None and wanted:
        logger.warning("Configured account {} was not found in the Kraken account list", wanted)
        return None
    if account_row is None:
        account_row = _upsert_account(session, accounts[0])
        _sync_agreements(session, accounts[0])

    sync_properties(session, client, settings, account_row.number)
    sync_meters(session, client, settings, account_row.number)
    return account_row


def sync_properties(
    session: Session,
    client: KrakenClient,
    settings: Settings,
    account_number: str,
) -> list[Property]:
    """Read the properties with their market location (MaLo) and meter."""
    payload = client.execute(queries.ACCOUNT_PROPERTIES, {"accountNumber": account_number})
    account = (payload.get("data") or {}).get("account") or {}
    rows: list[Property] = []

    for raw in account.get("properties") or []:
        property_id = raw.get("id")
        if property_id is None:
            continue
        row = session.get(Property, str(property_id))
        if row is None:
            row = Property(id=str(property_id))
            session.add(row)
        row.account_number = account_number
        row.label = raw.get("label")
        row.address = raw.get("address")

        malo = (raw.get("electricityMalos") or [{}])[0] or {}
        row.malo_id = malo.get("id")
        row.malo_number = malo.get("maloNumber")
        meters = malo.get("meters") or []
        if meters:
            meter = meters[0]
            row.meter_id = str(meter.get("id")) if meter.get("id") is not None else None
            row.meter_number = meter.get("number")
            row.melo_number = meter.get("meloNumber")
            row.has_smart_meter_gateway = meter.get("hasSmartMeterGateway")
            if row.meter_id:
                meter_row = session.get(Meter, row.meter_id)
                if meter_row is None:
                    meter_row = Meter(id=row.meter_id)
                    session.add(meter_row)
                meter_row.account_number = account_number
                meter_row.number = row.meter_number
                meter_row.melo_number = row.melo_number
                meter_row.has_smart_meter_gateway = row.has_smart_meter_gateway
                meter_row.updated_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        rows.append(row)

    session.commit()
    if rows:
        logger.info("Stored {} properties", len(rows))
    return rows


def primary_property(session: Session, settings: Settings) -> Property | None:
    """The property whose consumption is displayed."""
    statement = select(Property)
    if settings.account_number:
        statement = statement.where(Property.account_number == settings.account_number)
    properties = list(session.scalars(statement))
    if not properties:
        return None
    return properties[0]


def _store_identity(session: Session, viewer: dict) -> None:
    """Remember which user the token belongs to (shown by login/token-info)."""
    row = session.get(Token, 1)
    if row is None:
        row = Token(id=1)
        session.add(row)
    row.viewer_email = viewer.get("email")
    row.viewer_name = viewer.get("fullName")
    row.updated_at = datetime.utcnow()
    session.commit()


def _upsert_account(session: Session, raw: dict) -> Account:
    number = raw.get("number")
    row = session.get(Account, number)
    if row is None:
        row = Account(number=number)
        session.add(row)
    row.brand = raw.get("brand")
    row.status = raw.get("status")
    row.billing_name = raw.get("billingName")
    row.billing_email = raw.get("billingEmail")
    row.balance_cents = raw.get("balance") or 0
    row.overdue_cents = raw.get("overdueBalance")
    row.raw = json.dumps(raw, default=str)
    row.updated_at = datetime.utcnow()
    session.commit()
    return row


def _sync_agreements(session: Session, raw_account: dict) -> None:
    connection = raw_account.get("marketSupplyAgreements") or {}
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        agreement_id = node.get("id")
        if agreement_id is None:
            continue
        row = session.get(Agreement, str(agreement_id))
        if row is None:
            row = Agreement(id=str(agreement_id))
            session.add(row)
        row.account_number = raw_account.get("number")
        product = node.get("product") or {}
        row.product_code = product.get("code")
        row.product_name = product.get("displayName") or product.get("fullName")
        code = (product.get("code") or "").lower()
        row.is_time_of_use = row.is_time_of_use or any(hint in code for hint in TIME_OF_USE_HINTS)
        supply_point = node.get("supplyPoint") or {}
        row.supply_point_id = supply_point.get("id")
        row.supply_point_external_id = supply_point.get("externalIdentifier")
        row.supply_point_market = supply_point.get("marketName")
        row.valid_from = parse_datetime(node.get("agreedFrom"))
        row.valid_to = parse_datetime(node.get("agreedTo"))
        row.is_active = bool(node.get("isActive", True))
        row.updated_at = datetime.utcnow()
    session.commit()


def sync_meters(
    session: Session,
    client: KrakenClient,
    settings: Settings,
    account_number: str,
) -> list[Meter]:
    melo_number = settings.melo_number or _melo_number(session, account_number)
    if not melo_number:
        logger.info("No melo number known - skipping the meter query")
        return []

    try:
        payload = client.execute(
            queries.ELECTRICITY_METERS,
            {"accountNumber": account_number, "meloNumber": melo_number},
        )
    except KrakenError as exc:
        # not available for customer accounts - the meter comes with the property
        logger.info("electricityMeters not available: {}", exc.message)
        return []
    connection = (payload.get("data") or {}).get("electricityMeters") or {}
    rows: list[Meter] = []
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        meter_id = node.get("id")
        if meter_id is None:
            continue
        row = session.get(Meter, str(meter_id))
        if row is None:
            row = Meter(id=str(meter_id))
            session.add(row)
        row.account_number = account_number
        row.number = node.get("number")
        row.melo_number = node.get("meloNumber")
        row.has_smart_meter_gateway = node.get("hasSmartMeterGateway")
        row.conversion_factor = node.get("conversionFactor")
        row.active_from = parse_datetime(node.get("activeFrom"))
        row.active_to = parse_datetime(node.get("activeTo"))
        row.updated_at = datetime.utcnow()
        rows.append(row)
    session.commit()
    return rows


def _melo_number(session: Session, account_number: str) -> str | None:
    """The melo number is the external identifier of the supply point."""
    statement = select(Agreement.supply_point_external_id).where(
        Agreement.account_number == account_number,
        Agreement.supply_point_external_id.is_not(None),
    )
    return session.scalar(statement)


def primary_agreement(session: Session, settings: Settings) -> Agreement | None:
    """The agreement of the observed electricity supply (dynamic tariff preferred)."""
    statement = select(Agreement)
    if settings.account_number:
        statement = statement.where(Agreement.account_number == settings.account_number)
    agreements = list(session.scalars(statement))
    if not agreements:
        return None

    def score(agreement: Agreement) -> tuple[int, int, int, int]:
        code = (agreement.product_code or "").lower()
        is_electric = int(any(hint in code for hint in ELECTRICITY_HINTS))
        is_tou = int(bool(agreement.is_time_of_use))
        is_active = int(bool(agreement.is_active))
        # electricity first, then active contracts, then the dynamic tariff
        return (is_electric and is_active, is_electric, is_active, is_tou)

    return sorted(agreements, key=score, reverse=True)[0]


def primary_meter(session: Session, settings: Settings) -> Meter | None:
    """The meter of the observed supply - the configured one wins."""
    meters = list(session.scalars(select(Meter)))
    if not meters:
        return None
    if settings.meter_id:
        for meter in meters:
            if meter.id == settings.meter_id:
                return meter

    def score(meter: Meter) -> tuple[int, int, datetime]:
        # active meters first, then the most recently synchronised one
        return (int(meter.active_to is None), int(meter.id == settings.meter_id), meter.updated_at)

    return sorted(meters, key=score, reverse=True)[0]


def active_meter_id(session: Session, settings: Settings) -> str | None:
    """The meter the dashboard shows (configured one or the active one)."""
    meter = primary_meter(session, settings)
    return meter.id if meter else None
