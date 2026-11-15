"""End-to-end test of the synchronisation pipeline against a mocked Kraken API.

The payloads mirror the real schema (verified by live introspection), so the
test covers the complete path: login -> account -> prices -> readings ->
intervals -> costs -> dashboard.
"""

from __future__ import annotations

import httpx
import respx
from sqlalchemy import select

from oenergy.api.client import KrakenClient
from oenergy.config import Settings
from oenergy.db.models import Agreement, DailyStat, Interval, Meter, Price
from oenergy.services.sync import run_all

ENDPOINT = "https://api.oeg-kraken.energy/v1/graphql/"

VIEWER = {
    "data": {
        "viewer": {
            "id": "user-1",
            "email": "demo@example.org",
            "fullName": "Demo Haushalt",
            "accounts": [
                {
                    "number": "A-12345678",
                    "brand": "Octopus Energy Germany",
                    "status": "ACTIVE",
                    "balance": -1250,
                    "overdueBalance": None,
                    "billingName": "Demo Haushalt",
                    "billingEmail": "demo@example.org",
                    "marketSupplyAgreements": {
                        "edges": [
                            {
                                "node": {
                                    "id": "42",
                                    "isActive": True,
                                    "agreedFrom": "2026-01-01T00:00:00+00:00",
                                    "agreedTo": None,
                                    "supplyPoint": {
                                        "id": "9",
                                        "externalIdentifier": "DE12345678",
                                        "marketName": "Default-DE",
                                    },
                                    "product": {
                                        "code": "DYNAMIC_OCTOPUS_DE",
                                        "displayName": "Dynamic Octopus",
                                        "fullName": "Dynamic Octopus",
                                        "isTimeOfUse": True,
                                    },
                                }
                            }
                        ]
                    },
                }
            ],
        }
    }
}

AGREEMENT = {
    "data": {
        "agreement": {
            "id": "42",
            "isActive": True,
            "validFrom": "2026-01-01T00:00:00+00:00",
            "validTo": None,
            "netMonthlyStandingCharge": 380.0,
            "standingChargeGrossRateInformation": [
                {"date": "2026-01-01", "vatRate": 0.19, "grossRate": 452.2, "rateValidToDate": None}
            ],
            "unitRateInformation": {
                "rates": [
                    {
                        "timeslotName": "DAY",
                        "netUnitRateCentsPerKwh": 10.0,
                        "latestGrossUnitRateCentsPerKwh": 11.9,
                    }
                ]
            },
            "unitRateForecast": [
                {
                    "validFrom": "2026-11-15T00:00:00+00:00",
                    "validTo": "2026-11-16T00:00:00+00:00",
                    "unitRateInformation": {
                        "rates": [
                            {
                                "timeslotName": "DAY",
                                "netUnitRateCentsPerKwh": 9.5,
                                "latestGrossUnitRateCentsPerKwh": 11.3,
                            }
                        ]
                    },
                }
            ],
            "product": {
                "code": "DYNAMIC_OCTOPUS_DE",
                "displayName": "Dynamic Octopus",
                "isTimeOfUse": True,
            },
        }
    }
}

AGREEMENT_RATES = {
    "data": {
        "agreementRates": {
            "totalCount": 2,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "cursor": "c0",
                    "node": {
                        "name": "Dynamic Octopus",
                        "category": "WORK",
                        "pricePerUnit": "0.0900",
                        "unit": "EUR/kWh",
                        "currency": "EUR",
                        "validityPeriod": {
                            "start": "2026-11-13T22:00:00+00:00",
                            "end": "2026-11-13T22:15:00+00:00",
                        },
                    },
                },
                {
                    "cursor": "c0b",
                    "node": {
                        "name": "Dynamic Octopus",
                        "category": "WORK",
                        "pricePerUnit": "0.0950",
                        "unit": "EUR/kWh",
                        "currency": "EUR",
                        "validityPeriod": {
                            "start": "2026-11-13T22:15:00+00:00",
                            "end": "2026-11-13T22:30:00+00:00",
                        },
                    },
                },
                {
                    "cursor": "c1",
                    "node": {
                        "name": "Dynamic Octopus",
                        "category": "WORK",
                        "pricePerUnit": "0.0950",
                        "unit": "EUR/kWh",
                        "currency": "EUR",
                        "validityPeriod": {
                            "start": "2026-11-15T09:00:00+00:00",
                            "end": "2026-11-15T09:15:00+00:00",
                        },
                    },
                },
                {
                    "cursor": "c2",
                    "node": {
                        "name": "Dynamic Octopus",
                        "category": "WORK",
                        "pricePerUnit": "0.1000",
                        "unit": "EUR/kWh",
                        "currency": "EUR",
                        "validityPeriod": {
                            "start": "2026-11-15T09:15:00+00:00",
                            "end": "2026-11-15T09:30:00+00:00",
                        },
                    },
                },
            ],
        }
    }
}

EPEX = {
    "data": {
        "epexDayAheadPrices": {
            "totalCount": 2,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "cursor": "e1",
                    "node": {
                        "periodStart": "2026-11-15T09:00:00+00:00",
                        "periodEnd": "2026-11-15T09:15:00+00:00",
                        "value": 87.5,
                        "unit": "EUR/MWh",
                    },
                },
                {
                    "cursor": "e2",
                    "node": {
                        "periodStart": "2026-11-15T09:15:00+00:00",
                        "periodEnd": "2026-11-15T09:30:00+00:00",
                        "value": 92.0,
                        "unit": "EUR/MWh",
                    },
                },
            ],
        }
    }
}

METERS = {
    "data": {
        "electricityMeters": {
            "edges": [
                {
                    "node": {
                        "id": "77",
                        "number": "DE12345678",
                        "meloNumber": "DE12345678",
                        "hasSmartMeterGateway": True,
                        "conversionFactor": "1.0",
                        "activeFrom": "2026-01-01T00:00:00+00:00",
                        "activeTo": None,
                    }
                }
            ]
        }
    }
}

READINGS = {
    "data": {
        "electricityMeterReadings": {
            "totalCount": 3,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "cursor": "r1",
                    "node": {
                        "value": "12000.000",
                        "readAt": "2026-11-13T22:00:00+00:00",
                        "registerObisCode": "1-0:1.8.0",
                        "typeOfRead": "BILLING",
                        "origin": "SUPPLIER",
                        "status": "OK",
                        "meterId": 77,
                        "registerType": "ACTIVE",
                    },
                },
                {
                    "cursor": "r2",
                    "node": {
                        "value": "12000.500",
                        "readAt": "2026-11-13T22:15:00+00:00",
                        "registerObisCode": "1-0:1.8.0",
                        "typeOfRead": "INTERIM",
                        "origin": "SUPPLIER",
                        "status": "OK",
                        "meterId": 77,
                        "registerType": "ACTIVE",
                    },
                },
                {
                    "cursor": "r3",
                    "node": {
                        "value": "12001.000",
                        "readAt": "2026-11-13T22:30:00+00:00",
                        "registerObisCode": "1-0:1.8.0",
                        "typeOfRead": "INTERIM",
                        "origin": "SUPPLIER",
                        "status": "OK",
                        "meterId": 77,
                        "registerType": "ACTIVE",
                    },
                },
            ],
        }
    }
}

ACCOUNT_PROPERTIES = {
    "data": {
        "account": {
            "number": "A-12345678",
            "properties": [
                {
                    "id": "prop-1",
                    "label": "Zuhause",
                    "address": "Teststraße 1",
                    "electricityMalos": [
                        {
                            "id": "malo-1",
                            "maloNumber": "DE12345678",
                            "supplyPointId": "9",
                            "meters": [
                                {
                                    "id": "77",
                                    "number": "DE12345678",
                                    "meterType": "MME",
                                    "meloNumber": "DE12345678",
                                    "hasSmartMeterGateway": True,
                                    "shouldReceiveSmartMeterData": True,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
}

MEASUREMENTS = {
    "data": {
        "property": {
            "id": "prop-1",
            "measurements": {
                "totalCount": 2,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "cursor": "m1",
                        "node": {
                            "source": "Amphio",
                            "value": "0.250",
                            "unit": "kwh",
                            "readAt": "2026-11-13T22:15:00+00:00",
                            "startAt": "2026-11-13T22:00:00+00:00",
                            "endAt": "2026-11-13T22:15:00+00:00",
                        },
                    },
                    {
                        "cursor": "m2",
                        "node": {
                            "source": "Amphio",
                            "value": "0.500",
                            "unit": "kwh",
                            "readAt": "2026-11-13T22:30:00+00:00",
                            "startAt": "2026-11-13T22:15:00+00:00",
                            "endAt": "2026-11-13T22:30:00+00:00",
                        },
                    },
                ],
            },
        }
    }
}

TOKEN = {
    "data": {
        "obtainKrakenToken": {
            "token": "token-1",
            "refreshToken": "refresh-1",
            "refreshExpiresIn": 3600,
        }
    }
}

BILLS = {
    "data": {
        "account": {
            "number": "A-12345678",
            "balance": -1250,
            "overdueBalance": None,
            "bills": {
                "edges": [
                    {
                        "node": {
                            "id": "bill-1",
                            "billType": "ELECTRICITY",
                            "fromDate": "2026-10-01",
                            "toDate": "2026-10-31",
                            "issuedDate": "2026-11-02",
                            "status": "ISSUED",
                            "totalCharges": {"netTotal": 4500, "taxTotal": 855, "grossTotal": 5355},
                        }
                    }
                ]
            },
        }
    }
}


def _handler(request: httpx.Request) -> httpx.Response:
    body = httpx.Response
    payload = request.content.decode()
    if "obtainKrakenToken" in payload:
        return body(200, json=TOKEN)
    if "ViewerAccounts" in payload:
        return body(200, json=VIEWER)
    if "AgreementDetails" in payload:
        return body(200, json=AGREEMENT)
    if "AgreementRates" in payload:
        return body(200, json=AGREEMENT_RATES)
    if "EpexDayAhead" in payload:
        return body(200, json=EPEX)
    if "ElectricityMeters" in payload:
        return body(200, json=METERS)
    if "ElectricityMeterReadings" in payload:
        return body(200, json=READINGS)
    if "AccountBills" in payload:
        return body(200, json=BILLS)
    if "AccountProperties" in payload:
        return body(200, json=ACCOUNT_PROPERTIES)
    if "PropertyMeasurements" in payload:
        return body(200, json=MEASUREMENTS)
    if "SupplyPointReadings" in payload:
        return body(200, json={"data": {"supplyPoint": None}})
    return body(200, json={"data": {}})


def _settings() -> Settings:
    return Settings(
        db_url="sqlite:///:memory:",
        octopus_email="demo@example.org",
        octopus_password="secret",
        api_url=ENDPOINT,
        api_min_interval_seconds=0.0,
        timezone="Europe/Berlin",
        readings_history_days=5,
        readings_backfill_days=3,
        readings_max_window_days=7,
        gap_retry_minutes=60,
        energy_unit="auto",
    )


@respx.mock
def test_full_sync_pipeline(database) -> None:
    respx.post(ENDPOINT).mock(side_effect=_handler)

    settings = _settings()
    runs = run_all(database=database, settings=settings)

    assert all(run.status == "ok" for run in runs), [run.message for run in runs]

    session = database.session()

    agreement = session.scalar(select(Agreement))
    assert agreement is not None
    assert agreement.product_code == "DYNAMIC_OCTOPUS_DE"
    assert agreement.is_time_of_use is True
    assert agreement.gross_monthly_standing_charge_ct == 452.2
    assert agreement.vat_rate == 0.19

    meter = session.scalar(select(Meter))
    assert meter is not None
    assert meter.number == "DE12345678"

    # 0.0950 EUR/kWh -> 9.5 ct/kWh
    customer_prices = list(session.scalars(select(Price).where(Price.scope == "customer")))
    rates = [price for price in customer_prices if price.source == "agreement_rates"]
    assert len(rates) == 4
    assert {rate.net_ct_per_kwh for rate in rates} == {9.0, 9.5, 10.0}
    # no gross price in the API -> derived from the VAT rate
    nine = next(rate for rate in rates if rate.net_ct_per_kwh == 9.0)
    assert round(nine.gross_ct_per_kwh or 0, 4) == 10.71

    epex_prices = list(session.scalars(select(Price).where(Price.scope == "epex")))
    assert len(epex_prices) == 2
    assert epex_prices[0].net_ct_per_kwh == 8.75

    from oenergy.db.models import Property, ReadingWindow

    prop = session.scalar(select(Property))
    assert prop is not None
    assert prop.malo_number == "DE12345678"
    assert prop.meter_id == "77"

    windows = list(session.scalars(select(ReadingWindow)))
    assert windows
    assert all(window.queried_at is not None for window in windows)

    # 0.75 kWh over two 15 minute slots (the property delivers the intervals)
    intervals = list(session.scalars(select(Interval)))
    assert len(intervals) == 2
    assert round(sum(interval.kwh for interval in intervals), 6) == 0.75

    stats = list(session.scalars(select(DailyStat)))
    assert stats
    # 0.25 kWh at 9.0 ct and 0.5 kWh at 9.5 ct (net) -> gross with 19% VAT
    day_stat = next(stat for stat in stats if stat.energy_cost_cents > 0)
    assert round(day_stat.energy_cost_cents, 4) == 8.33
    assert day_stat.standing_cost_cents > 0

    session.close()


@respx.mock
def test_second_sync_does_not_requery_covered_ranges(database) -> None:
    route = respx.post(ENDPOINT).mock(side_effect=_handler)
    settings = _settings()

    run_all(database=database, settings=settings)
    readings_calls = len(
        [call for call in route.calls if "ElectricityMeterReadings" in call.request.content.decode()]
    )

    run_all(database=database, settings=settings)
    readings_calls_after = len(
        [call for call in route.calls if "ElectricityMeterReadings" in call.request.content.decode()]
    )

    # already fetched ranges are not requested again
    assert readings_calls_after == readings_calls


@respx.mock
def test_sync_survives_api_errors(database) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.content.decode()
        if "obtainKrakenToken" in payload:
            return httpx.Response(200, json=TOKEN)
        if "PropertyMeasurements" in payload:
            return httpx.Response(200, json={"errors": [{"message": "boom"}]})
        return _handler(request)

    respx.post(ENDPOINT).mock(side_effect=handler)

    runs = run_all(database=database, settings=_settings())
    names = {run.job: run for run in runs}

    # the failing job is logged, the others still run
    assert names["readings"].status == "error"
    assert names["prices"].status == "ok"
    assert names["account"].status == "ok"


@respx.mock
def test_diagnose_reports_the_account(database) -> None:
    from oenergy.services.diagnose import diagnose, format_report

    respx.post(ENDPOINT).mock(side_effect=_handler)
    settings = _settings()
    run_all(database=database, settings=settings)

    with KrakenClient(settings, database.session, httpx.Client()) as client, database.session() as session:
        report = diagnose(session, client, settings)

    assert report["errors"] == []
    assert report["account"]["number"] == "A-12345678"
    assert report["agreements"][0]["is_time_of_use"] is True
    assert report["prices"]["unitRateForecast"]["ok"] is True
    assert report["prices"]["epexDayAheadPrices"]["count"] == 2
    assert report["readings"]["count"] == 2
    assert report["properties"][0]["malo_number"] == "DE12345678"

    text = format_report(report)
    assert "Konto A-12345678" in text
    assert "Agreement 42" in text
