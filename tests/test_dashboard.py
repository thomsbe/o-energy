from oenergy.services.dashboard import (
    CHEAP_LIMIT,
    EXPENSIVE_LIMIT,
    classify_price,
    price_hue,
)


def test_price_classification() -> None:
    css, level, label = classify_price(20.65)
    assert css == "is-cheap"
    assert label == "günstig"
    assert 0 < level <= 1

    css, level, label = classify_price(CHEAP_LIMIT)
    assert css == "is-mid"
    assert label == "normal"

    css, level, label = classify_price(EXPENSIVE_LIMIT)
    assert css == "is-expensive"
    assert label == "teuer"

    css, level, label = classify_price(None)
    assert css == "is-empty"
    assert label == "keine Daten"


def test_price_intensity_grows_with_the_distance() -> None:
    _, low, _ = classify_price(25.0)
    _, high, _ = classify_price(10.0)
    assert high > low

    _, low, _ = classify_price(41.0)
    _, high, _ = classify_price(70.0)
    assert high > low


def test_hue_scale_runs_from_green_to_red() -> None:
    cheap = price_hue(10.0)
    normal = price_hue(33.0)
    expensive = price_hue(70.0)

    # Grün -> Gelb -> Rot: der Farbton sinkt (0 = Rot, 120 = Grün)
    assert cheap > normal > expensive
    assert 90 <= cheap <= 180
    assert 40 <= normal <= 75
    assert 0 <= expensive <= 20

    # die Verlaufskurve ist monoton
    values = [price_hue(v) for v in range(5, 80, 5)]
    assert values == sorted(values, reverse=True)


def test_month_forecast_combines_known_costs_with_the_remaining_days(session) -> None:
    from datetime import date, timedelta

    from oenergy.db.models import DailyStat, Meter

    session.add(Meter(id="m"))
    today = date.today()
    month_start = today.replace(day=1)
    for offset in range(10):
        day = month_start + timedelta(days=offset)
        session.add(
            DailyStat(
                meter_id="m",
                day=day,
                kwh=8.0,
                cost_cents=400.0,
                energy_cost_cents=380.0,
                standing_cost_cents=20.0,
                received_slots=96,
                expected_slots=96,
                is_final=True,
            )
        )
    session.commit()

    from oenergy.config import Settings
    from oenergy.services.dashboard import cost_summary

    settings = Settings(db_url="sqlite:///:memory:", timezone="Europe/Berlin")
    summary = cost_summary(session, settings)

    assert summary["month_cents"] == 4000.0
    assert summary["basis_days"] == 7  # die letzten 7 Tage sind die Basis
    assert summary["daily_avg_cents"] == 400.0
    assert summary["remaining_days"] > 0
    # Prognose = bereits angefallen + offene Tage × Tagesschnitt
    assert summary["forecast_month_cents"] == (
        summary["month_cents"] + summary["remaining_days"] * summary["daily_avg_cents"]
    )
    # bei gleichmäßigen Tageskosten ist die Unsicherheit 0
    assert summary["forecast_uncertainty_cents"] == 0.0
