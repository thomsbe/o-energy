from __future__ import annotations

from datetime import datetime, timedelta

from oenergy.services.consumption import RawReading, derive_intervals, detect_unit


def _readings(values: list[tuple[datetime, float]]) -> list[RawReading]:
    return [
        RawReading(
            read_at=at,
            value=value,
            register_obis_code="1-0:1.8.0",
            type_of_read=None,
            origin=None,
            status=None,
        )
        for at, value in values
    ]


def test_detect_unit_prefers_kwh_for_small_deltas() -> None:
    assert detect_unit([0.2, 0.25, 0.3], "auto") == "kwh"


def test_detect_unit_switches_to_wh_for_large_deltas() -> None:
    assert detect_unit([150.0, 210.0, 180.0], "auto") == "wh"


def test_detect_unit_honours_configuration() -> None:
    assert detect_unit([0.2, 0.25], "wh") == "wh"


def test_cumulative_readings_become_slot_consumption() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    series = [(start + timedelta(minutes=15 * index), 100.0 + 0.25 * index) for index in range(4)]
    slots = derive_intervals(_readings(series), slot_minutes=15)

    assert len(slots) == 3
    assert all(abs(slot.kwh - 0.25) < 1e-9 for slot in slots)
    # the consumption of 00:00-00:15 is stored in the slot starting at 00:00
    assert slots[0].slot_start == datetime(2026, 11, 10, 0, 0)


def test_wh_registers_are_scaled_to_kwh() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    series = [(start + timedelta(minutes=15 * index), 10000.0 + 250.0 * index) for index in range(3)]
    slots = derive_intervals(_readings(series), slot_minutes=15, unit="auto")

    assert all(abs(slot.kwh - 0.25) < 1e-9 for slot in slots)


def test_gaps_are_spread_over_the_covered_slots() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    # two readings one hour apart -> 1 kWh spread over four slots
    series = [(start, 100.0), (start + timedelta(hours=1), 101.0)]
    slots = derive_intervals(_readings(series), slot_minutes=15)

    assert len(slots) == 4
    assert all(abs(slot.kwh - 0.25) < 1e-9 for slot in slots)
    assert slots[0].slot_start == datetime(2026, 11, 10, 0, 0)


def test_negative_differences_are_ignored() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    series = [
        (start, 100.0),
        (start + timedelta(minutes=15), 110.0),
        (start + timedelta(minutes=30), 5.0),  # meter replaced
        (start + timedelta(minutes=45), 115.0),
    ]
    slots = derive_intervals(_readings(series), slot_minutes=15, mode="cumulative")

    # the jump to the new register and the difference across the change are dropped
    assert [round(slot.kwh, 3) for slot in slots] == [10.0]


def test_interval_readings_are_used_as_is() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    series = [
        (start, 1.0),
        (start + timedelta(minutes=15), 0.5),
        (start + timedelta(minutes=30), 2.0),
    ]
    slots = derive_intervals(_readings(series), slot_minutes=15, mode="interval")

    assert [round(slot.kwh, 3) for slot in slots] == [1.0, 0.5, 2.0]


def test_auto_mode_detects_cumulative_series() -> None:
    start = datetime(2026, 11, 10, 0, 0)
    series = [(start + timedelta(minutes=15 * index), 100.0 + index) for index in range(5)]
    slots = derive_intervals(_readings(series), slot_minutes=15, mode="auto")

    assert [round(slot.kwh, 3) for slot in slots] == [1.0, 1.0, 1.0, 1.0]
