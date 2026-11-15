from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from oenergy.db.models import Price
from oenergy.services.pricing import effective_at, group_rates

TZ = ZoneInfo("Europe/Berlin")
START = datetime(2026, 11, 14, 10, 0)


def _rate(**kwargs) -> Price:
    defaults = {
        "scope": "customer",
        "source": "agreement_rates",
        "rate_name": "rate",
        "starts_at": START,
        "ends_at": START + timedelta(hours=1),
        "net_ct_per_kwh": 10.0,
        "gross_ct_per_kwh": 11.9,
    }
    defaults.update(kwargs)
    return Price(**defaults)


def test_rates_of_one_window_are_summed_up() -> None:
    rows = [
        _rate(rate_name="BASE", net_ct_per_kwh=20.65, gross_ct_per_kwh=24.5735),
        _rate(rate_name="DYNAMIC", net_ct_per_kwh=3.35, gross_ct_per_kwh=3.9865),
    ]

    price = effective_at(rows, START, vat=0.19, tz=TZ)

    assert price is not None
    assert price.net_ct == 24.0
    assert round(price.gross_ct, 4) == 28.56
    assert sorted(price.rate_names) == ["BASE", "DYNAMIC"]


def test_agreement_rates_win_over_the_forecast() -> None:
    rows = [
        _rate(source="forecast", rate_name="BASE", net_ct_per_kwh=99.0),
        _rate(source="agreement_rates", rate_name="BASE", net_ct_per_kwh=20.65),
    ]

    windows = group_rates(rows, 0.19)

    assert len(windows) == 1
    assert windows[0].source == "agreement_rates"
    assert windows[0].net_ct == 20.65


def test_static_rate_is_the_fallback_without_a_window() -> None:
    rows = [
        _rate(
            source="static",
            rate_name="BASE",
            starts_at=datetime(2020, 1, 1),
            ends_at=datetime(2030, 1, 1),
            net_ct_per_kwh=20.65,
            gross_ct_per_kwh=24.5735,
        )
    ]

    price = effective_at(rows, START, vat=0.19, tz=TZ)

    assert price is not None
    assert price.source == "static"
    assert price.net_ct == 20.65


def test_static_rate_with_activation_rule_is_only_used_in_its_window() -> None:
    rows = [
        _rate(
            source="static",
            rate_name="NIGHT",
            starts_at=datetime(2020, 1, 1),
            ends_at=datetime(2030, 1, 1),
            net_ct_per_kwh=20.65,
            gross_ct_per_kwh=24.5735,
            active_from="02:00:00",
            active_to="06:00:00",
        )
    ]

    # 10:00 UTC = 11:00 local -> outside the activation window
    outside = effective_at(rows, START, vat=0.19, tz=TZ)
    # 02:30 local (01:30 UTC) -> inside the activation window
    inside = effective_at(rows, datetime(2026, 11, 14, 1, 30), vat=0.19, tz=TZ)

    assert outside is not None and outside.net_ct == 20.65
    assert inside is not None and inside.net_ct == 20.65


def test_no_price_without_any_rate() -> None:
    assert effective_at([], START, vat=0.19, tz=TZ) is None
