"""Synthetic data generator.

Used to develop and test the dashboard without live API credentials.  The
generated data mimics the behaviour of the real API, especially the ~24 hour
delay of the utility: the values of *today* are not available yet.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from oenergy.config import Settings
from oenergy.db.models import Account, Agreement, Meter, MeterReading, Price
from oenergy.services.completeness import recompute
from oenergy.services.consumption import rebuild_intervals
from oenergy.services.costs import compute_costs

DEMO_ACCOUNT = "A-DEMO1"
DEMO_AGREEMENT = "demo-agreement"
DEMO_METER = "demo-meter"


def seed(session: Session, settings: Settings, days: int = 30) -> dict:
    """Fill the database with plausible demo data."""
    rng = random.Random(42)
    now = datetime.utcnow()
    slot = settings.slot_minutes

    account = session.get(Account, DEMO_ACCOUNT)
    if account is None:
        account = Account(number=DEMO_ACCOUNT)
        session.add(account)
    account.brand = "Octopus Energy Germany"
    account.status = "ACTIVE"
    account.billing_name = "Demo Haushalt"
    account.billing_email = "demo@example.org"
    account.balance_cents = 0
    account.updated_at = now

    agreement = session.get(Agreement, DEMO_AGREEMENT)
    if agreement is None:
        agreement = Agreement(id=DEMO_AGREEMENT)
        session.add(agreement)
    agreement.account_number = DEMO_ACCOUNT
    agreement.product_code = "DYNAMIC_OCTOPUS_DE"
    agreement.product_name = "Dynamic Octopus"
    agreement.is_time_of_use = True
    agreement.melo_number = "DE00012345678"
    agreement.supply_point_external_id = "DE00012345678"
    agreement.is_active = True
    agreement.net_monthly_standing_charge_ct = 857.98
    agreement.gross_monthly_standing_charge_ct = 1021.0
    agreement.vat_rate = 0.19
    agreement.updated_at = now

    meter = session.get(Meter, DEMO_METER)
    if meter is None:
        meter = Meter(id=DEMO_METER)
        session.add(meter)
    meter.account_number = DEMO_ACCOUNT
    meter.number = "DE00012345678"
    meter.melo_number = "DE00012345678"
    meter.has_smart_meter_gateway = True
    meter.updated_at = now

    session.commit()

    # --- prices (customer price + EPEX) ---------------------------------
    start = now - timedelta(days=days)
    end = now + timedelta(days=2)
    price_rows: list[Price] = []
    epex_rows: list[Price] = []
    cursor = start.replace(second=0, microsecond=0)
    cursor = cursor.replace(minute=(cursor.minute // slot) * slot)
    while cursor < end:
        hour = cursor.hour + cursor.minute / 60
        # daily price shape with a cheap valley around 03:00 and an expensive peak at 19:00
        shape = (
            math.sin((hour - 3) / 24 * 2 * math.pi) * 0.55 + math.cos((hour - 19) / 24 * 2 * math.pi) * 0.35
        )
        epex_ct = 6.0 + shape * 6.5 + rng.uniform(-1.2, 1.2)
        markup = 2.4
        epex_rows.append(
            {
                "scope": "epex",
                "source": "epex",
                "rate_name": "epex",
                "starts_at": cursor,
                "ends_at": cursor + timedelta(minutes=slot),
                "net_ct_per_kwh": round(epex_ct, 4),
                "gross_ct_per_kwh": None,
                "unit": "EUR/MWh",
                "updated_at": now,
            }
        )
        # dynamicOctopus: base component + variable component (exchange price)
        base_ct = 20.65
        variable_ct = epex_ct + markup
        price_rows.append(
            {
                "scope": "customer",
                "source": "agreement_rates",
                "rate_name": "BASE",
                "agreement_id": DEMO_AGREEMENT,
                "starts_at": cursor,
                "ends_at": cursor + timedelta(minutes=slot),
                "net_ct_per_kwh": base_ct,
                "gross_ct_per_kwh": round(base_ct * 1.19, 4),
                "unit": "ct/kWh",
                "updated_at": now,
            }
        )
        price_rows.append(
            {
                "scope": "customer",
                "source": "agreement_rates",
                "rate_name": "DYNAMIC",
                "agreement_id": DEMO_AGREEMENT,
                "starts_at": cursor,
                "ends_at": cursor + timedelta(minutes=slot),
                "net_ct_per_kwh": round(variable_ct, 4),
                "gross_ct_per_kwh": round(variable_ct * 1.19, 4),
                "unit": "ct/kWh",
                "updated_at": now,
            }
        )
        cursor += timedelta(minutes=slot)

    session.query(Price).delete()
    session.add_all([Price(**row) for row in epex_rows])
    session.add_all([Price(**row) for row in price_rows])
    session.commit()

    # --- raw readings (cumulative register, delivered with ~24h delay) ----
    session.query(MeterReading).delete()
    readings = []
    value = 12000.0
    cursor = (now - timedelta(days=days)).replace(second=0, microsecond=0)
    cursor = cursor.replace(minute=(cursor.minute // slot) * slot)
    delivery_cutoff = now - timedelta(hours=16)  # values of the last 16h are not delivered yet
    while cursor < delivery_cutoff:
        hour = cursor.hour + cursor.minute / 60
        # ~20 kWh per day, with a morning and an evening peak
        base = 0.16 + 0.16 * math.exp(-((hour - 7.5) ** 2) / 6) + 0.30 * math.exp(-((hour - 19) ** 2) / 4)
        value += max(0.0, rng.gauss(base, 0.05))
        readings.append(
            {
                "meter_id": DEMO_METER,
                "register_obis_code": "1-0:1.8.0",
                "read_at": cursor,
                "value": round(value, 4),
                "type_of_read": "BILLING" if cursor.hour == 0 and cursor.minute == 0 else "INTERIM",
                "origin": "SUPPLIER",
                "status": "OK",
                "received_at": min(cursor + timedelta(hours=24), now),
            }
        )
        cursor += timedelta(minutes=slot)

    session.add_all([MeterReading(**row) for row in readings])
    session.commit()

    rebuild_intervals(session, settings)
    compute_costs(session, settings)
    recompute(session, settings)

    return {
        "account": DEMO_ACCOUNT,
        "prices": len(price_rows) + len(epex_rows),
        "readings": len(readings),
    }
