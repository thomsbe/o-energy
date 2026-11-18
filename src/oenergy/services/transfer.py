"""Export und Import der Datenbestände.

Der Export ist unabhängig vom Datenbankschema: die Werte werden als JSON
geschrieben (optional gzip-komprimiert) und beim Import wieder anhand ihrer
natürlichen Schlüssel eingespielt. So lässt sich ein Bestand auch dann
zurückspielen, wenn sich die Datenbankstruktur zwischenzeitlich geändert hat.
"""

from __future__ import annotations

import gzip
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Date, DateTime, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from oenergy.db.models import (
    Account,
    Agreement,
    Bill,
    DailyStat,
    Interval,
    Meter,
    MeterReading,
    Price,
    Property,
    ReadingWindow,
)

#: Formatversion des Exports - bei inkompatiblen Änderungen hochzählen
EXPORT_FORMAT = 1

#: SQLite hat ein Limit für die Anzahl der Parameter je Statement
CHUNK_SIZE = 400

EXPORT_TABLES = (
    "accounts",
    "agreements",
    "properties",
    "meters",
    "meter_readings",
    "intervals",
    "prices",
    "daily_stats",
    "bills",
    "reading_windows",
)


def _upsert(
    session: Session,
    model: Any,
    values: list[dict[str, Any]],
    index_elements: list[str],
    update_columns: dict[str, Any],
) -> int:
    """Spielt Datensätze in Blöcken ein (SQLite begrenzt die Parameterzahl)."""
    for index in range(0, len(values), CHUNK_SIZE):
        chunk = values[index : index + CHUNK_SIZE]
        statement = sqlite_insert(model).values(chunk)
        statement = statement.on_conflict_do_update(
            index_elements=index_elements,
            set_={name: statement.excluded[name] for name in update_columns},
        )
        session.execute(statement)
    return len(values)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _row_values(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def export_data(session: Session) -> dict[str, Any]:
    """Alle relevanten Daten als serialisierbares Dictionary."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for name in EXPORT_TABLES:
        model = {
            "accounts": Account,
            "agreements": Agreement,
            "properties": Property,
            "meters": Meter,
            "meter_readings": MeterReading,
            "intervals": Interval,
            "prices": Price,
            "daily_stats": DailyStat,
            "bills": Bill,
            "reading_windows": ReadingWindow,
        }[name]
        rows = list(session.scalars(select(model)))
        tables[name] = [_row_values(row) for row in rows]

    return {
        "format": "o-energy-export",
        "format_version": EXPORT_FORMAT,
        "exported_at": datetime.utcnow().isoformat(),
        "tables": tables,
    }


def write_export(session: Session, path: str | Path, *, compress: bool = True) -> Path:
    """Export als Datei schreiben (.json oder .json.gz)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(export_data(session), indent=None, default=_json_default)
    if compress or target.suffix == ".gz":
        if target.suffix != ".gz":
            target = target.with_suffix(target.suffix + ".gz")
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        target.write_text(payload, encoding="utf-8")
    return target


def read_export(path: str | Path) -> dict[str, Any]:
    """Export-Datei lesen (gzip oder reines JSON)."""
    source = Path(path)
    if source.suffix == ".gz":
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(source.read_text(encoding="utf-8"))


def _prepare(model: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Zeitwerte eines Exportdatensatzes zurück in Python-Objekte wandeln.

    Die Umwandlung richtet sich nach den Spaltentypen des Modells - damit
    bleiben auch neue Felder automatisch korrekt.
    """
    data = dict(row)
    for column in model.__table__.columns:
        if column.name not in data:
            continue
        value = data[column.name]
        if value in (None, ""):
            continue
        if isinstance(column.type, DateTime):
            data[column.name] = _parse_datetime(value)
        elif isinstance(column.type, Date):
            data[column.name] = _parse_date(value)
    return data


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def import_data(session: Session, payload: dict[str, Any]) -> dict[str, int]:
    """Import eines Exports - die Datensätze werden anhand ihrer Schlüssel
    aktualisiert (idempotent, ein Import ändert nichts doppelt)."""
    if payload.get("format") != "o-energy-export":
        raise ValueError("Unbekanntes Exportformat")
    version = int(payload.get("format_version", 0))
    if version > EXPORT_FORMAT:
        raise ValueError(
            f"Export-Format {version} ist neuer als unterstützt ({EXPORT_FORMAT}) - bitte aktualisieren"
        )

    tables = payload.get("tables") or {}
    counts: dict[str, int] = {}

    for row in tables.get("accounts", []):
        data = _prepare(Account, row)
        session.merge(Account(**data))
    counts["accounts"] = len(tables.get("accounts", []))

    for row in tables.get("agreements", []):
        data = _prepare(Agreement, row)
        session.merge(Agreement(**data))
    counts["agreements"] = len(tables.get("agreements", []))

    for row in tables.get("properties", []):
        data = _prepare(Property, row)
        session.merge(Property(**data))
    counts["properties"] = len(tables.get("properties", []))

    for row in tables.get("meters", []):
        data = _prepare(Meter, row)
        session.merge(Meter(**data))
    counts["meters"] = len(tables.get("meters", []))

    for row in tables.get("bills", []):
        data = _prepare(Bill, row)
        session.merge(Bill(**data))
    counts["bills"] = len(tables.get("bills", []))

    counts["meter_readings"] = _import_readings(session, tables.get("meter_readings", []))
    counts["intervals"] = _import_intervals(session, tables.get("intervals", []))
    counts["prices"] = _import_prices(session, tables.get("prices", []))
    counts["daily_stats"] = _import_daily_stats(session, tables.get("daily_stats", []))
    counts["reading_windows"] = _import_reading_windows(session, tables.get("reading_windows", []))

    session.commit()
    return counts


def _import_readings(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = []
    for row in rows:
        data = dict(row)
        data["read_at"] = _parse_datetime(data.get("read_at"))
        data["received_at"] = _parse_datetime(data.get("received_at")) or datetime.utcnow()
        data.pop("id", None)
        values.append(data)
    return _upsert(
        session,
        MeterReading,
        values,
        ["meter_id", "register_obis_code", "read_at"],
        {
            "value": "value",
            "type_of_read": "type_of_read",
            "origin": "origin",
            "status": "status",
        },
    )


def _import_intervals(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = []
    for row in rows:
        data = dict(row)
        data["slot_start"] = _parse_datetime(data.get("slot_start"))
        data["slot_end"] = _parse_datetime(data.get("slot_end"))
        data["received_at"] = _parse_datetime(data.get("received_at")) or datetime.utcnow()
        data.pop("id", None)
        values.append(data)
    return _upsert(
        session,
        Interval,
        values,
        ["meter_id", "slot_start"],
        {
            "slot_end": "slot_end",
            "kwh": "kwh",
            "n_readings": "n_readings",
            "source": "source",
        },
    )


def _import_prices(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = []
    for row in rows:
        data = dict(row)
        data["starts_at"] = _parse_datetime(data.get("starts_at"))
        data["ends_at"] = _parse_datetime(data.get("ends_at"))
        data["updated_at"] = _parse_datetime(data.get("updated_at")) or datetime.utcnow()
        data.pop("id", None)
        values.append(data)
    return _upsert(
        session,
        Price,
        values,
        ["scope", "source", "starts_at", "rate_name"],
        {
            "ends_at": "ends_at",
            "agreement_id": "agreement_id",
            "net_ct_per_kwh": "net_ct_per_kwh",
            "gross_ct_per_kwh": "gross_ct_per_kwh",
            "unit": "unit",
            "active_from": "active_from",
            "active_to": "active_to",
        },
    )


def _import_daily_stats(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = []
    for row in rows:
        data = dict(row)
        data["day"] = _parse_date(data.get("day"))
        data["finalized_at"] = _parse_datetime(data.get("finalized_at"))
        data["updated_at"] = _parse_datetime(data.get("updated_at")) or datetime.utcnow()
        data.pop("id", None)
        values.append(data)
    return _upsert(
        session,
        DailyStat,
        values,
        ["meter_id", "day"],
        {
            "kwh": "kwh",
            "cost_cents": "cost_cents",
            "energy_cost_cents": "energy_cost_cents",
            "standing_cost_cents": "standing_cost_cents",
            "expected_slots": "expected_slots",
            "received_slots": "received_slots",
            "estimated_slots": "estimated_slots",
            "is_final": "is_final",
            "finalized_at": "finalized_at",
        },
    )


def _import_reading_windows(session: Session, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    values = []
    for row in rows:
        data = dict(row)
        data["start_at"] = _parse_datetime(data.get("start_at"))
        data["end_at"] = _parse_datetime(data.get("end_at"))
        data["queried_at"] = _parse_datetime(data.get("queried_at")) or datetime.utcnow()
        data.pop("id", None)
        values.append(data)
    return _upsert(
        session,
        ReadingWindow,
        values,
        ["meter_id", "start_at", "end_at"],
        {
            "queried_at": "queried_at",
            "rows_found": "rows_found",
            "status": "status",
        },
    )


def write_env_value(target: str | Path, key: str, value: str, *, escape: bool = True) -> Path:
    """Schreibt einen Schlüssel in eine .env-Datei.

    ``escape=True`` maskiert die ``$`` für Docker Compose (dort bedeutet ``$$``
    ein einzelnes ``$``). Die App normalisiert beide Formen beim Laden, für
    Kompatibilität mit älteren Versionen wird hier escaped geschrieben.
    """
    path = Path(target)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    entry = f"{key}={value.replace('$', '$$') if escape else value}"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            lines[index] = entry
            break
    else:
        lines.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
