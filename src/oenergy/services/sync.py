from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.client import KrakenClient
from oenergy.config import Settings, get_settings
from oenergy.db.base import Database, get_database
from oenergy.db.models import Bill, SyncRun
from oenergy.services import completeness, consumption, costs, prices
from oenergy.services.account import primary_agreement, sync_account
from oenergy.utils import parse_date, parse_decimal

logger = logging.getLogger(__name__)


def _record(session: Session, run: SyncRun) -> None:
    session.add(run)
    session.commit()


def job_account(session: Session, client: KrakenClient, settings: Settings) -> int:
    account = sync_account(session, client, settings)
    return 1 if account else 0


def job_prices(session: Session, client: KrakenClient, settings: Settings) -> int:
    return prices.sync_prices(session, client, settings)


def job_readings(session: Session, client: KrakenClient, settings: Settings) -> int:
    stored = consumption.sync_readings(session, client, settings)
    consumption.rebuild_intervals(session, settings)
    # the supply point delivers the real 15 minute values - they win over the
    # values derived from the cumulative register
    stored += consumption.sync_supply_point_intervals(session, client, settings)
    costs.compute_costs(session, settings)
    completeness.recompute(session, settings)
    return stored


def job_finalize(session: Session, client: KrakenClient, settings: Settings) -> int:
    """Re-run the daily aggregation - used after the 08:00 delivery window."""
    costs.compute_costs(session, settings)
    return completeness.recompute(session, settings)


def job_bills(session: Session, client: KrakenClient, settings: Settings) -> int:
    agreement = primary_agreement(session, settings)
    if agreement is None or not agreement.account_number:
        return 0
    payload = client.execute(
        queries.ACCOUNT_BILLS,
        {"accountNumber": agreement.account_number, "first": 24},
    )
    account = (payload.get("data") or {}).get("account") or {}
    connection = account.get("bills") or {}
    count = 0
    for edge in connection.get("edges") or []:
        node = edge.get("node") or {}
        bill_id = node.get("id")
        if not bill_id:
            continue
        row = session.get(Bill, str(bill_id))
        if row is None:
            row = Bill(id=str(bill_id))
            session.add(row)
        charges = node.get("totalCharges") or {}
        gross = charges.get("grossTotal")
        if gross is None:
            gross = node.get("grossAmount")
        row.account_number = agreement.account_number
        row.period_start = parse_date(node.get("fromDate"))
        row.period_end = parse_date(node.get("toDate"))
        row.issued_at = parse_date(node.get("issuedDate"))
        row.total_cents = parse_decimal(gross)
        row.raw = json.dumps(node, default=str)
        row.updated_at = datetime.utcnow()
        count += 1
    session.commit()
    return count


JOBS: dict[str, Callable[[Session, KrakenClient, Settings], int]] = {
    "account": job_account,
    "prices": job_prices,
    "readings": job_readings,
    "finalize": job_finalize,
    "bills": job_bills,
}


def run_job(
    job_name: str,
    *,
    database: Database | None = None,
    client: KrakenClient | None = None,
    settings: Settings | None = None,
) -> SyncRun:
    """Execute one sync job and log the outcome in ``sync_runs``."""
    settings = settings or get_settings()
    database = database or get_database()
    owns_client = client is None
    client = client or KrakenClient(settings, database.session)

    session = database.session()
    run = SyncRun(job=job_name, status="running", started_at=datetime.utcnow())
    try:
        job = JOBS[job_name]
        items = job(session, client, settings)
        run.status = "ok"
        run.items = items
        run.message = None
    except Exception as exc:
        logger.exception("Sync job %s failed", job_name)
        run.status = "error"
        run.message = f"{type(exc).__name__}: {exc}"[:2000]
        items = 0
    finally:
        run.finished_at = datetime.utcnow()
        _record(session, run)
        session.close()
        if owns_client:
            client.close()

    return run


def run_all(*, database: Database | None = None, settings: Settings | None = None) -> list[SyncRun]:
    settings = settings or get_settings()
    database = database or get_database()
    runs: list[SyncRun] = []
    client = KrakenClient(settings, database.session)
    try:
        for job_name in ("account", "prices", "readings", "finalize", "bills"):
            runs.append(run_job(job_name, database=database, client=client, settings=settings))
    finally:
        client.close()
    return runs


def latest_runs(session: Session, limit: int = 20) -> list[SyncRun]:
    from sqlalchemy import select

    return list(session.scalars(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)))


def dump_raw(
    session: Session,
    client: KrakenClient,
    settings: Settings,
    target: str = "all",
) -> dict[str, Any]:
    """Dump raw API answers - used to explore the API during setup (probe command)."""
    out: dict[str, Any] = {}
    if target in ("all", "account"):
        out["viewer_accounts"] = client.execute(queries.VIEWER_ACCOUNTS)
    agreement = primary_agreement(session, settings)
    if agreement is not None:
        out["agreement"] = client.execute(queries.AGREEMENT_DETAILS, {"id": agreement.id})
        out["agreement_rates"] = client.execute(
            queries.AGREEMENT_RATES, {"agreementId": agreement.id, "first": 20}
        )
    if target in ("all", "prices"):
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC).replace(tzinfo=None)
        out["epex"] = client.execute(
            queries.EPEX_DAY_AHEAD,
            {
                "periodStart": (now - timedelta(days=1)).isoformat() + "Z",
                "periodEnd": (now + timedelta(days=2)).isoformat() + "Z",
                "first": 10,
            },
        )
    if target in ("all", "readings") and agreement is not None and agreement.account_number:
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        out["meters"] = client.execute(
            queries.ELECTRICITY_METERS,
            {"accountNumber": agreement.account_number, "meloNumber": settings.melo_number},
        )
        out["meter_readings"] = client.execute(
            queries.ELECTRICITY_METER_READINGS,
            {
                "accountNumber": agreement.account_number,
                "meterId": settings.meter_id or "1",
                "readFrom": (now - timedelta(days=7)).isoformat() + "Z",
                "readTo": now.isoformat() + "Z",
                "first": 20,
            },
        )
    return out
