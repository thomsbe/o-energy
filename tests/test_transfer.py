from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from oenergy.db.models import Account, Agreement, Interval, Meter, Price, Property
from oenergy.services.transfer import (
    EXPORT_FORMAT,
    export_data,
    import_data,
    read_export,
    write_export,
)


def _seed(session) -> None:
    session.add(Account(number="A-1", brand="Test", status="ACTIVE", balance_cents=-100))
    session.add(
        Agreement(
            id="1",
            account_number="A-1",
            product_code="TEST",
            is_time_of_use=True,
            supply_point_external_id="DE1",
            valid_from=datetime(2026, 1, 1),
        )
    )
    session.add(Property(id="p1", account_number="A-1", malo_number="DE1"))
    session.add(Meter(id="m1", account_number="A-1", number="DE1"))
    session.add(
        Interval(
            meter_id="m1",
            slot_start=datetime(2026, 11, 10, 12, 0),
            slot_end=datetime(2026, 11, 10, 12, 15),
            kwh=0.42,
            source="measured",
            n_readings=1,
        )
    )
    session.add(
        Price(
            scope="customer",
            source="agreement_rates",
            rate_name="BASE",
            starts_at=datetime(2026, 11, 10, 12, 0),
            ends_at=datetime(2026, 11, 10, 12, 15),
            net_ct_per_kwh=17.35,
            gross_ct_per_kwh=20.65,
        )
    )
    session.commit()


def test_export_contains_all_relevant_tables(session) -> None:
    _seed(session)

    payload = export_data(session)

    assert payload["format"] == "o-energy-export"
    assert payload["format_version"] == EXPORT_FORMAT
    assert payload["tables"]["intervals"][0]["kwh"] == 0.42
    assert payload["tables"]["prices"][0]["rate_name"] == "BASE"
    # die Zeitwerte sind serialisierbar
    json.dumps(payload, default=str)


def test_export_and_import_round_trip(session, tmp_path) -> None:
    _seed(session)
    target = write_export(session, tmp_path / "export.json.gz")

    # Export zurückspielen: die Werte werden unverändert übernommen
    payload = read_export(target)
    counts = import_data(session, payload)

    assert counts["intervals"] == 1
    assert counts["prices"] == 1
    assert counts["accounts"] == 1
    assert session.scalar(select(func.count()).select_from(Interval)) == 1
    assert session.scalar(select(Interval.kwh)) == 0.42


def test_import_is_idempotent(session, tmp_path) -> None:
    _seed(session)
    target = write_export(session, tmp_path / "export.json.gz")
    payload = read_export(target)

    import_data(session, payload)
    import_data(session, payload)

    assert session.scalar(select(func.count()).select_from(Interval)) == 1
    assert session.scalar(select(func.count()).select_from(Price)) == 1


def test_import_rejects_unknown_format(session) -> None:
    import pytest

    with pytest.raises(ValueError):
        import_data(session, {"format": "something-else"})

    with pytest.raises(ValueError):
        import_data(session, {"format": "o-energy-export", "format_version": 999})


def test_export_can_be_written_uncompressed(session, tmp_path) -> None:
    _seed(session)
    target = write_export(session, tmp_path / "export.json", compress=False)

    assert target.suffix == ".json"
    data = json.loads(target.read_text())
    assert data["tables"]["accounts"][0]["number"] == "A-1"


def test_exported_dates_survive_the_round_trip(session, tmp_path) -> None:
    _seed(session)
    target = write_export(session, tmp_path / "export.json.gz")
    payload = read_export(target)

    import_data(session, payload)
    agreement = session.get(Agreement, "1")
    assert agreement is not None
    assert agreement.valid_from == datetime(2026, 1, 1)
    interval = session.scalar(select(Interval))
    assert interval is not None
    assert interval.slot_start == datetime(2026, 11, 10, 12, 0)
    assert interval.slot_start + timedelta(minutes=15) == interval.slot_end
