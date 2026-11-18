from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update

from oenergy.config import get_settings
from oenergy.db.base import ReadOnlyViolation, readonly_session
from oenergy.db.models import Account
from oenergy.services import mcp_tools


def _all_tools(session) -> dict:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz).replace(microsecond=0).isoformat()
    return {
        "status": mcp_tools.get_status(session, settings),
        "consumption": mcp_tools.get_consumption(session, settings, days=5),
        "costs": mcp_tools.get_costs(session, settings, days=5),
        "prices": mcp_tools.get_prices(session, settings, hours=24),
        "cheapest": mcp_tools.get_cheapest_windows(session, settings, days=2),
        "price_at": mcp_tools.get_price_at(session, settings, now),
        "month": mcp_tools.get_monthly_overview(session, settings),
        "account": mcp_tools.get_account(session, settings),
    }


def test_tools_are_json_serialisable_and_contain_no_identifiers(seeded, session) -> None:
    payload = _all_tools(session)
    text = json.dumps(payload, ensure_ascii=False)

    assert "A-DEMO1" not in text
    assert "DE00012345678" not in text
    assert "demo@example.org" not in text
    assert "Demo Haushalt" not in text
    assert "demo-meter" not in text

    assert payload["consumption"]["total_kwh"] > 0
    assert payload["costs"]["total_cost_eur"] > 0
    assert payload["prices"]["windows"]
    assert payload["cheapest"]["windows"]
    assert payload["price_at"]["found"] is True
    assert payload["month"]["kwh"] > 0
    assert payload["account"]["tariff"]["name"] == "Dynamic Octopus"
    assert payload["account"]["pricing"]["vat_percent"] == 19.0


def test_times_are_local_iso_with_offset(seeded, session) -> None:
    settings = get_settings()
    prices = mcp_tools.get_prices(session, settings, hours=24)
    status = mcp_tools.get_status(session, settings)

    assert prices["generated_at"].endswith(("+01:00", "+02:00"))
    assert all(window["starts_at"].endswith(("+01:00", "+02:00")) for window in prices["windows"])
    assert status["generated_at"].endswith(("+01:00", "+02:00"))


def test_consumption_and_costs_never_include_today(seeded, session) -> None:
    settings = get_settings()
    today = datetime.now(ZoneInfo(settings.timezone)).date()
    consumption = mcp_tools.get_consumption(session, settings, days=7)
    costs = mcp_tools.get_costs(session, settings, days=7)

    assert all(row["day"] != today.isoformat() for row in consumption["daily"])
    assert all(row["day"] != today.isoformat() for row in costs["daily"])
    assert consumption["to"] == (today - timedelta(days=1)).isoformat()


def test_price_at_uses_local_time_without_offset(seeded, session) -> None:
    settings = get_settings()
    local_now = datetime.now(ZoneInfo(settings.timezone)).replace(microsecond=0)
    result = mcp_tools.get_price_at(session, settings, local_now.strftime("%Y-%m-%dT%H:%M:%S"))

    assert result["found"] is True
    assert result["price"]["gross_ct_per_kwh"] is not None
    assert result["when"].endswith(("+01:00", "+02:00"))


def test_price_at_rejects_invalid_input(seeded, session) -> None:
    with pytest.raises(ValueError):
        mcp_tools.get_price_at(session, get_settings(), "not-a-date")


def test_monthly_overview_rejects_invalid_month(seeded, session) -> None:
    with pytest.raises(ValueError):
        mcp_tools.get_monthly_overview(session, get_settings(), month="2026-13")


def test_tools_work_without_data(database, session) -> None:
    settings = get_settings()
    assert mcp_tools.get_status(session, settings)["data"]["interval_count"] == 0
    assert mcp_tools.get_consumption(session, settings)["days_with_data"] == 0
    assert mcp_tools.get_costs(session, settings)["total_cost_eur"] == 0.0
    assert mcp_tools.get_prices(session, settings)["current"] is None
    assert mcp_tools.get_cheapest_windows(session, settings)["windows"] == []
    assert mcp_tools.get_monthly_overview(session, settings)["days_with_data"] == 0
    assert mcp_tools.get_account(session, settings)["data_available"] is False


def test_readonly_session_rejects_orm_writes(database) -> None:
    with readonly_session(database) as session:
        session.add(Account(number="NEW-ACCOUNT"))
        with pytest.raises(ReadOnlyViolation):
            session.flush()


def test_readonly_session_rejects_bulk_updates(seeded, database) -> None:
    with readonly_session(database) as session, pytest.raises(ReadOnlyViolation):
        session.execute(update(Account).values(status="CHANGED"))


def test_price_at_accepts_utc(seeded, session) -> None:
    settings = get_settings()
    when = datetime.now(UTC).replace(microsecond=0).isoformat()
    result = mcp_tools.get_price_at(session, settings, when)
    assert result["when"].endswith(("+01:00", "+02:00"))
