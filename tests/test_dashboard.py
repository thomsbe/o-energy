from oenergy.services.dashboard import CHEAP_LIMIT, EXPENSIVE_LIMIT, classify_price


def test_price_classification() -> None:
    css, level, label = classify_price(20.65)
    assert css == "is-cheap"
    assert label == "günstig"
    assert 0 < level <= 1

    css, level, label = classify_price(CHEAP_LIMIT)
    assert css == "is-mid"
    assert label == "mittel"

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
