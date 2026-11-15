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
