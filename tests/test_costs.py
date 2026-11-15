from __future__ import annotations

from datetime import datetime, timedelta

from oenergy.config import Settings
from oenergy.db.models import Account, Agreement, DailyStat, Interval, Meter, Price
from oenergy.services.costs import PriceBook, compute_costs


def _settings() -> Settings:
    return Settings(db_url="sqlite:///:memory:", timezone="Europe/Berlin", slot_minutes=15)


def _seed(session, price_ct: float = 30.0, kwh: float = 2.0) -> None:
    start = datetime(2026, 11, 14, 10, 0)
    session.add(Account(number="A1"))
    session.add(Meter(id="m1"))
    session.add(
        Agreement(
            id="a1",
            account_number="A1",
            gross_monthly_standing_charge_ct=452.2,
            net_monthly_standing_charge_ct=380.0,
            vat_rate=0.19,
        )
    )
    session.add(
        Price(
            scope="customer",
            source="agreement_rates",
            agreement_id="a1",
            starts_at=start,
            ends_at=start + timedelta(hours=4),
            net_ct_per_kwh=price_ct,
            gross_ct_per_kwh=price_ct,
            unit="ct/kWh",
        )
    )
    session.add(
        Interval(
            meter_id="m1",
            slot_start=start,
            slot_end=start + timedelta(minutes=15),
            kwh=kwh,
            source="measured",
            n_readings=1,
        )
    )
    session.commit()


def test_energy_cost_is_kwh_times_price(session) -> None:
    settings = _settings()
    _seed(session, price_ct=30.0, kwh=2.0)

    compute_costs(session, settings)

    stat = session.query(DailyStat).one()
    # 2 kWh * 30 ct = 60 ct
    assert round(stat.energy_cost_cents, 4) == 60.0
    # standing charge is shared across the days of the month
    assert stat.standing_cost_cents > 0
    assert round(stat.cost_cents, 4) == round(stat.energy_cost_cents + stat.standing_cost_cents, 4)


def test_price_book_prefers_customer_prices(session) -> None:
    start = datetime(2026, 11, 14, 10, 0)
    session.add(
        Price(
            scope="epex",
            source="epex",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            net_ct_per_kwh=5.0,
        )
    )
    session.add(
        Price(
            scope="customer",
            source="agreement_rates",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            net_ct_per_kwh=9.0,
            gross_ct_per_kwh=10.71,
        )
    )
    session.commit()

    book = PriceBook(session, start, start + timedelta(hours=2))
    price = book.at(start)

    assert price is not None
    assert price.net_ct_per_kwh == 9.0


def test_price_book_falls_back_to_epex(session) -> None:
    start = datetime(2026, 11, 14, 10, 0)
    session.add(
        Price(
            scope="epex",
            source="epex",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            net_ct_per_kwh=5.0,
        )
    )
    session.commit()

    book = PriceBook(session, start, start + timedelta(hours=2))
    price = book.at(start)

    assert price is not None
    assert price.scope == "epex"


def test_price_book_composes_base_component_and_epex(session) -> None:
    start = datetime(2026, 11, 14, 10, 0)
    session.add(
        Price(
            scope="epex",
            source="epex",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            net_ct_per_kwh=5.0,
        )
    )
    session.add(
        Price(
            scope="customer",
            source="agreement_rates",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            net_ct_per_kwh=7.0,
            gross_ct_per_kwh=8.33,
        )
    )
    # the fixed base component of the tariff (valid the whole time)
    session.add(
        Price(
            scope="customer",
            source="static",
            rate_name="STANDARD",
            starts_at=datetime(2020, 1, 1),
            ends_at=datetime(2030, 1, 1),
            net_ct_per_kwh=17.35,
            gross_ct_per_kwh=20.6465,
        )
    )
    later = start + timedelta(hours=2)
    session.add(
        Price(
            scope="epex",
            source="epex",
            rate_name="epex",
            starts_at=later,
            ends_at=later + timedelta(hours=1),
            net_ct_per_kwh=6.0,
        )
    )
    session.commit()

    book = PriceBook(session, start, later + timedelta(hours=1))
    price = book.at(later)

    assert price is not None
    assert price.source == "static+epex"
    # base component (17.35) + exchange price (6.00) = 23.35 ct/kWh netto
    assert round(price.net_ct_per_kwh or 0, 4) == 23.35
    assert round(price.gross_ct_per_kwh or 0, 4) == 27.7865


def test_standing_charge_comes_from_the_active_agreement(session) -> None:
    """Regressionstest: ein beendeter Vertrag darf den Grundpreis nicht liefern."""
    from datetime import date

    from oenergy.config import Settings
    from oenergy.db.models import Account, Agreement, DailyStat, Meter

    session.add(Account(number="A1"))
    session.add(Meter(id="m1", account_number="A1"))
    session.add(
        Agreement(
            id="aktiv",
            account_number="A1",
            product_code="ELECTRICITY_DYNAMIC",
            is_active=True,
            gross_monthly_standing_charge_ct=1021.26,
            net_monthly_standing_charge_ct=858.2,
            vat_rate=0.19,
        )
    )
    session.add(
        Agreement(
            id="beendet",
            account_number="A1",
            product_code="ELECTRICITY_DYNAMIC_LIGHT",
            is_active=False,
            gross_monthly_standing_charge_ct=None,
            updated_at=datetime(2026, 11, 15, 12, 0, 0),
        )
    )
    today = date.today()
    session.add(
        DailyStat(
            meter_id="m1",
            day=today,
            kwh=10.0,
            energy_cost_cents=500.0,
            received_slots=96,
            expected_slots=96,
            is_final=True,
        )
    )
    session.commit()

    from oenergy.services.costs import compute_costs

    settings = Settings(db_url="sqlite:///:memory:", timezone="Europe/Berlin")
    compute_costs(session, settings)

    stat = session.query(DailyStat).filter(DailyStat.day == today).one()
    # 1021,26 ct / 31 Tage = 32,94 ct
    assert stat.standing_cost_cents > 30
    assert stat.cost_cents == stat.energy_cost_cents + stat.standing_cost_cents
