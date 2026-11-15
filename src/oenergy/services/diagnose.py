"""Diagnostics for the live setup.

``oenergy diagnose`` walks through every step of the pipeline and reports what
the account actually delivers - the quickest way to find out whether the price
and consumption queries work for a real contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.client import KrakenClient
from oenergy.config import Settings
from oenergy.db.models import Agreement, DailyStat, Interval, Meter, Price, Property
from oenergy.services.account import primary_agreement, sync_account
from oenergy.services.completeness import data_state
from oenergy.utils import to_local


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}".replace(",", ".")


def diagnose(session: Session, client: KrakenClient, settings: Settings) -> dict:
    """Collect everything that is interesting about the configured account."""
    report: dict[str, Any] = {"errors": [], "warnings": []}

    # --- account --------------------------------------------------------
    account = sync_account(session, client, settings)
    if account is None:
        report["errors"].append("Kein Konto gefunden - sind die Zugangsdaten korrekt?")
        return report
    report["account"] = {
        "number": account.number,
        "status": account.status,
        "billing_name": account.billing_name,
        "balance_cents": account.balance_cents,
    }

    agreements = list(session.scalars(select(Agreement)))
    report["agreements"] = [
        {
            "id": agreement.id,
            "product_code": agreement.product_code,
            "product_name": agreement.product_name,
            "is_time_of_use": agreement.is_time_of_use,
            "supply_point": agreement.supply_point_external_id,
            "market": agreement.supply_point_market,
            "is_active": agreement.is_active,
            "valid_from": to_local(agreement.valid_from),
            "standing_charge_ct": agreement.gross_monthly_standing_charge_ct,
            "vat_rate": agreement.vat_rate,
        }
        for agreement in agreements
    ]
    if not agreements:
        report["errors"].append("Kein Agreement gefunden - der Tarif wird nicht geliefert.")

    meters = list(session.scalars(select(Meter)))
    report["meters"] = [
        {
            "id": meter.id,
            "number": meter.number,
            "melo_number": meter.melo_number,
            "smart_meter_gateway": meter.has_smart_meter_gateway,
            "active_to": to_local(meter.active_to),
        }
        for meter in meters
    ]
    if not meters:
        report["warnings"].append("Kein Zähler gefunden - Verbrauchsdaten sind nicht abrufbar.")

    # --- prices ---------------------------------------------------------
    agreement = primary_agreement(session, settings)
    price_report: dict[str, Any] = {}
    if agreement is not None:
        for name, query, variables in (
            (
                "unitRateForecast",
                queries.AGREEMENT_DETAILS,
                {"id": agreement.id},
            ),
            (
                "epexDayAheadPrices",
                queries.EPEX_DAY_AHEAD,
                {
                    "periodStart": (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z",
                    "periodEnd": (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z",
                    "first": 100,
                },
            ),
        ):
            try:
                payload = client.execute(query, variables)
                data = payload.get("data") or {}
                if name == "unitRateForecast":
                    count = len((data.get("agreement") or {}).get("unitRateForecast") or [])
                else:
                    key = {
                        "agreementRates": "agreementRates",
                        "epexDayAheadPrices": "epexDayAheadPrices",
                    }[name]
                    connection = data.get(key) or {}
                    count = connection.get("totalCount") or len(connection.get("edges") or [])
                price_report[name] = {"ok": True, "count": count}
            except Exception as exc:
                price_report[name] = {"ok": False, "error": str(exc)[:200]}
                report["warnings"].append(f"{name} nicht abrufbar: {exc}")
    report["prices"] = price_report

    stored_prices = list(session.scalars(select(Price)))
    report["stored_prices"] = {
        "total": len(stored_prices),
        "customer": len([p for p in stored_prices if p.scope == "customer"]),
        "epex": len([p for p in stored_prices if p.scope == "epex"]),
        "sources": sorted({p.source for p in stored_prices}),
    }

    # --- consumption ----------------------------------------------------
    intervals = list(session.scalars(select(Interval).order_by(Interval.slot_start)))
    report["readings"] = {
        "count": len(intervals),
        "first": to_local(intervals[0].slot_start) if intervals else None,
        "last": to_local(intervals[-1].slot_end) if intervals else None,
        "total_kwh": sum(row.kwh for row in intervals),
    }
    properties = list(session.scalars(select(Property)))
    report["properties"] = [
        {
            "id": row.id,
            "address": row.address,
            "malo_number": row.malo_number,
            "meter_number": row.meter_number,
            "has_smart_meter_gateway": row.has_smart_meter_gateway,
        }
        for row in properties
    ]

    # --- days -----------------------------------------------------------
    now = datetime.utcnow()
    stats = list(session.scalars(select(DailyStat).order_by(DailyStat.day.desc()).limit(7)))
    report["days"] = [
        {
            "day": stat.day.isoformat(),
            "state": data_state(stat.day, stat, now, settings),
            "kwh": stat.kwh,
            "cost_cents": stat.cost_cents,
            "is_final": stat.is_final,
            "received_slots": stat.received_slots,
        }
        for stat in stats
    ]

    return report


def format_report(report: dict) -> str:
    lines: list[str] = []
    lines.append("o-energy Diagnose")
    lines.append("=" * 60)

    for error in report.get("errors", []):
        lines.append(f"FEHLER: {error}")
    for warning in report.get("warnings", []):
        lines.append(f"WARNUNG: {warning}")

    account = report.get("account")
    if account:
        lines.append(
            f"Konto {account['number']} ({account['status']}, "
            f"Saldo {_fmt((account['balance_cents'] or 0) / 100)} €)"
        )

    for agreement in report.get("agreements", []):
        lines.append(
            f"  Agreement {agreement['id']}: {agreement['product_name']} "
            f"({agreement['product_code']}), "
            f"{'aktiv' if agreement.get('is_active') else 'beendet'}, "
            f"dynamisch={'ja' if agreement['is_time_of_use'] else 'nein'}, "
            f"SupplyPoint={agreement['supply_point']} ({agreement['market']}), "
            f"Grundpreis={_fmt(agreement['standing_charge_ct'])} ct/Monat, "
            f"USt={_fmt((agreement['vat_rate'] or 0) * 100)} %"
        )

    for meter in report.get("meters", []):
        lines.append(
            f"  Zähler {meter['id']}: {meter['number']} (melo {meter['melo_number']}, "
            f"Smart-Meter-Gateway={'ja' if meter['smart_meter_gateway'] else 'nein'})"
        )

    prices = report.get("prices", {})
    if prices:
        lines.append("Preisquellen:")
        for name, info in prices.items():
            if info.get("ok"):
                lines.append(f"  ✓ {name}: {info['count']} Einträge")
            else:
                lines.append(f"  ✗ {name}: {info.get('error')}")

    stored = report.get("stored_prices", {})
    if stored:
        lines.append(
            f"  in der DB: {stored['total']} Preise "
            f"(Kunde {stored['customer']}, EPEX {stored['epex']}, Quellen {stored['sources']})"
        )

    readings = report.get("readings", {})
    if readings:
        lines.append(
            f"Verbrauchsdaten: {readings['count']} Intervalle "
            f"({readings['first']} – {readings['last']}), "
            f"Summe {_fmt(readings.get('total_kwh'), 2)} kWh"
        )
        for prop in report.get("properties", []):
            lines.append(
                f"  Lieferpunkt {prop['malo_number'] or prop['id']} "
                f"(Zähler {prop['meter_number'] or '–'}, "
                f"Smart-Meter-Gateway={'ja' if prop['has_smart_meter_gateway'] else 'nein'})"
            )

    days = report.get("days", [])
    if days:
        lines.append("Tage (Status / Verbrauch / Kosten):")
        for day in days:
            lines.append(
                f"  {day['day']}  {day['state']:<12} "
                f"{_fmt(day['kwh']):>8} kWh  {_fmt((day['cost_cents'] or 0) / 100):>8} €"
            )

    return "\n".join(lines)
