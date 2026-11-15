from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from oenergy.config import get_settings
from oenergy.db.base import get_database
from oenergy.logging_config import setup_logging
from oenergy.services.identity import format_identity, token_status


def _configure_logging(verbose: bool) -> None:
    settings = get_settings()
    setup_logging(
        level="DEBUG" if verbose else settings.log_level,
        log_file=settings.log_file,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
    )


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "oenergy.main:app",
        host=args.host or settings.app_host,
        port=args.port or settings.app_port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from oenergy.api.client import KrakenClient
    from oenergy.services.sync import JOBS, run_all, run_job

    database = get_database()
    database.create_all()
    settings = get_settings()

    if args.job and args.job != "all":
        if args.job not in JOBS:
            print(f"Unknown job '{args.job}'. Available: {', '.join(sorted(JOBS))}", file=sys.stderr)
            return 2
        run = run_job(args.job, database=database)
        runs = [run]
    else:
        runs = run_all(database=database)

    if args.history_days:
        from oenergy.services.prices import sync_prices

        with database.session() as session, KrakenClient(settings, database.session) as client:
            count = sync_prices(session, client, settings, history_days=args.history_days)
        print(f"Historische Preise nachgeladen: {count}")

    for run in runs:
        state = "OK " if run.status == "ok" else "ERR"
        print(f"[{state}] {run.job:<9} items={run.items:<6} {run.message or ''}")
    return 0 if all(run.status == "ok" for run in runs) else 1


def cmd_probe(args: argparse.Namespace) -> int:
    """Dump the raw API answers - helps to find the right queries."""
    from oenergy.api.client import KrakenClient
    from oenergy.services.sync import dump_raw

    settings = get_settings()
    if not settings.has_credentials:
        print(
            "Keine Zugangsdaten konfiguriert.\n"
            "Setze OCTOPUS_EMAIL und OCTOPUS_PASSWORD (oder OCTOPUS_API_KEY) in der .env.",
            file=sys.stderr,
        )
        return 2

    database = get_database()
    database.create_all()
    with KrakenClient(settings, database.session) as client:
        payload = dump_raw(database.session(), client, settings, target=args.target)

    output = Path(args.out) if args.out else Path("probe.json")
    output.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Rohdaten geschrieben nach {output}")
    for key, value in payload.items():
        if isinstance(value, dict):
            data = value.get("data") or {}
            summary = ", ".join(
                f"{key}: {len(value) if isinstance(value, list) else type(value).__name__}"
                for key, value in data.items()
            )
            print(f"  {key:<18} {summary}")
    return 0


def _local(moment: datetime | None) -> datetime | None:
    """Naive UTC-Zeit in die lokale Zeit umrechnen."""
    if moment is None:
        return None
    from zoneinfo import ZoneInfo

    from oenergy.config import get_settings

    tz = ZoneInfo(get_settings().timezone)
    return moment.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None)


def _format_duration(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return "unbekannt"
    now = now or datetime.utcnow()
    delta = moment - now
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "abgelaufen"
    if seconds < 3600:
        return f"noch {seconds // 60} Minuten"
    if seconds < 86400:
        return f"noch {seconds // 3600} Stunden"
    return f"noch {seconds // 86400} Tage"


def cmd_login(args: argparse.Namespace) -> int:
    """One time login - afterwards the password is no longer needed."""
    from oenergy.api.client import KrakenClient

    settings = get_settings()
    if not settings.has_credentials:
        print(
            "Für die Anmeldung werden die Zugangsdaten gebraucht.\n"
            "Setze OCTOPUS_EMAIL und OCTOPUS_PASSWORD (oder OCTOPUS_API_KEY) in der .env "
            "- nach dem Login kann das Passwort wieder entfernt werden.",
            file=sys.stderr,
        )
        return 2

    database = get_database()
    database.create_all()
    with KrakenClient(settings, database.session) as client:
        info = client.login()
        identity = _load_identity(client=client, settings=settings, database=database)

    print("Login erfolgreich.")
    print(
        f"  Access-Token  gültig bis {info['access_expires_at']} "
        f"({_format_duration(info['access_expires_at'])})"
    )
    if info.get("has_refresh_token"):
        print(
            f"  Refresh-Token gültig bis {info['refresh_expires_at']} "
            f"({_format_duration(info['refresh_expires_at'])})"
        )
        print(
            "  → Das Passwort wird nicht mehr gebraucht: OCTOPUS_PASSWORD kann aus der .env entfernt werden."
        )
    else:
        print("  → Kein Refresh-Token erhalten - das Passwort muss in der .env bleiben.")

    if identity and identity.get("known"):
        print()
        print(format_identity(identity))
    return 0


def _load_identity(
    client=None,
    settings=None,
    database=None,
) -> dict:
    """Identity from the database - optionally refreshed from the API."""
    from oenergy.services.account import sync_account
    from oenergy.services.identity import identity

    database = database or get_database()
    if client is not None and settings is not None:
        with database.session() as db_session:
            try:
                sync_account(db_session, client, settings)
            except Exception as exc:
                print(f"Kontodaten konnten nicht geladen werden: {exc}", file=sys.stderr)
            return identity(db_session)

    with database.session() as db_session:
        return identity(db_session)


def cmd_token_info(args: argparse.Namespace) -> int:
    """Show the lifetime of the stored tokens."""
    from oenergy.api.client import KrakenClient

    settings = get_settings()
    database = get_database()
    with KrakenClient(settings, database.session) as client:
        info = client.token_info()

    if not info.get("logged_in") and not info.get("has_refresh_token"):
        print("Noch kein Login erfolgt - mit 'oenergy login' anmelden.")
        return 1

    access_expiry = _local(info.get("access_expires_at"))
    refresh_expiry = _local(info.get("refresh_expires_at"))
    print(f"Access-Token  : {'vorhanden' if info.get('logged_in') else '–'}")
    print(f"  gültig bis  : {access_expiry} ({_format_duration(info.get('access_expires_at'))})")
    print(f"Refresh-Token : {'vorhanden' if info.get('has_refresh_token') else '–'}")
    print(f"  gültig bis  : {refresh_expiry} ({_format_duration(info.get('refresh_expires_at'))})")
    print(f"  Art         : {info.get('refresh_kind') or '–'}")
    print(f"zuletzt aktualisiert: {_local(info.get('updated_at'))}")

    identity = _load_identity()
    if identity.get("known"):
        print()
        print(format_identity(identity))
    else:
        print()
        print("Noch keine Kontodaten bekannt - mit 'oenergy login' oder 'oenergy sync account' nachladen.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    """Show the user and the account behind the stored token."""
    settings = get_settings()
    identity = _load_identity()
    if not identity.get("known"):
        print("Noch keine Kontodaten bekannt.")
        if not settings.has_credentials:
            print("Setze OCTOPUS_EMAIL und OCTOPUS_PASSWORD in der .env und führe 'oenergy login' aus.")
        return 1
    print(format_identity(identity))
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Prüft, welche Daten der Account tatsächlich liefert."""
    from oenergy.api.client import KrakenClient
    from oenergy.services.diagnose import diagnose, format_report

    settings = get_settings()
    database = get_database()
    database.create_all()

    # Für die Diagnose reicht der gespeicherte Token - das Refresh-Token genügt.
    if not settings.has_credentials:
        with database.session() as session:
            stored = token_status(session)
        if not stored.get("logged_in"):
            print(
                "Keine Zugangsdaten konfiguriert.\n"
                "Setze OCTOPUS_EMAIL und OCTOPUS_PASSWORD (oder OCTOPUS_API_KEY) in der .env.",
                file=sys.stderr,
            )
            return 2
        print(
            "Hinweis: Es sind keine Zugangsdaten hinterlegt - die Diagnose nutzt den gespeicherten Token.",
            file=sys.stderr,
        )

    with KrakenClient(settings, database.session) as client, database.session() as session:
        report = diagnose(session, client, settings)

    print(format_report(report))
    if args.out:
        output = Path(args.out)
        output.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nRohdaten geschrieben nach {output}")

    return 1 if report.get("errors") else 0


def cmd_set_password(args: argparse.Namespace) -> int:
    """Erzeugt den Passwort-Hash für die Anmeldung."""
    from getpass import getpass

    from oenergy.web.auth import hash_password

    password = args.password
    if not password:
        password = getpass("Passwort: ")
        if password != getpass("Passwort wiederholen: "):
            print("Die Passwörter stimmen nicht überein.", file=sys.stderr)
            return 2
    if len(password) < 8:
        print("Das Passwort sollte mindestens 8 Zeichen lang sein.", file=sys.stderr)
        return 2

    hashed = hash_password(password)

    if args.write_env:
        target = Path(args.env_file or ".env")
        lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        # Docker Compose interpoliert die .env - die $ müssen als $$ escaped werden
        entry = f"AUTH_PASSWORD_HASH={hashed.replace('$', '$$')}"
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith("AUTH_PASSWORD_HASH="):
                lines[index] = entry
                replaced = True
                break
        if not replaced:
            lines.append(entry)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Passwort-Hash in {target} geschrieben (für Docker Compose escaped).")
        print("Damit die Änderung wirkt: docker compose up -d --force-recreate")
        return 0

    print()
    print("Diese Zeile in die .env übernehmen (in Docker Compose mit $$ escapen!):")
    print()
    print(f"AUTH_PASSWORD_HASH={hashed.replace('$', '$$')}")
    print()
    print("Oder direkt schreiben lassen: oenergy set-password --write-env")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Datenbestand als JSON exportieren (für Backups und Umzug)."""
    from oenergy.services.transfer import write_export

    database = get_database()
    database.create_all()
    target = args.out or f"data/backups/o-energy-{datetime.utcnow():%Y%m%d-%H%M%S}.json.gz"
    with database.session() as session:
        written = write_export(session, target, compress=not args.no_compress)
    size_mb = written.stat().st_size / 1024 / 1024
    print(f"Export geschrieben: {written} ({size_mb:.2f} MB)")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Export zurückspielen (idempotent - vorhandene Daten werden aktualisiert)."""
    from oenergy.services.completeness import recompute
    from oenergy.services.costs import compute_costs
    from oenergy.services.transfer import import_data, read_export

    database = get_database()
    database.create_all()
    payload = read_export(args.file)
    with database.session() as session:
        counts = import_data(session, payload)

    settings = get_settings()
    with database.session() as session:
        compute_costs(session, settings)
        recompute(session, settings)

    print(f"Import aus {args.file}:")
    for name, count in counts.items():
        print(f"  {name:16} {count}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    from oenergy.services.demo import seed

    settings = get_settings()
    database = get_database()
    database.create_all()
    with database.session() as session:
        result = seed(session, settings, days=args.days)
    print(f"Demo-Daten erzeugt: {result}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Leert alle Tabellen - z.B. um Demo-Daten vor dem echten Sync zu entfernen."""
    from oenergy.db.models import Base

    if not args.yes:
        print("ACHTUNG: Alle Daten (Verbrauch, Preise, Kosten) werden gelöscht.")
        print("Mit --yes bestätigen.")
        return 2

    database = get_database()
    with database.session() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
    print("Datenbank geleert.")
    return 0


def cmd_init_db(args: argparse.Namespace) -> int:
    database = get_database()
    database.create_all()
    print(f"Datenbank bereit: {database.url}")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    settings = get_settings()
    print("Geplante Jobs:")
    print(f"  prices    : alle {settings.poll_interval_minutes} Minuten")
    print("  readings  : stündlich (Minute 7)")
    print("  finalize  : täglich 08:17 (nach der Lieferung um ~08:00)")
    print("  bills     : täglich 06:12")
    print("  account   : alle 12 Stunden")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oenergy", description="o-energy – Octopus Energy DE Dashboard")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the web application")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    sync = sub.add_parser("sync", help="run the API synchronisation")
    sync.add_argument("job", nargs="?", default="all", help="account|prices|readings|finalize|bills|all")
    sync.add_argument(
        "--history-days",
        type=int,
        default=0,
        help="Börsenpreise für die Historie nachladen (z.B. 30)",
    )
    sync.set_defaults(func=cmd_sync)

    probe = sub.add_parser("probe", help="dump the raw API answers")
    probe.add_argument("--target", default="all", help="all|account|prices|readings")
    probe.add_argument("--out", default=None)
    probe.set_defaults(func=cmd_probe)

    set_password = sub.add_parser("set-password", help="create the password hash for the login")
    set_password.add_argument("password", nargs="?", default=None, help="Passwort (ohne Angabe: interaktiv)")
    set_password.add_argument(
        "--write-env",
        action="store_true",
        help="den Hash direkt in die .env schreiben (mit $$ escaped)",
    )
    set_password.add_argument("--env-file", default=".env", help="Zieldatei (Standard: .env)")
    set_password.set_defaults(func=cmd_set_password)

    export = sub.add_parser("export", help="export the data as JSON (backup)")
    export.add_argument("--out", default=None, help="Zieldatei (Standard: data/backups/...)")
    export.add_argument("--no-compress", action="store_true", help="ohne gzip schreiben")
    export.set_defaults(func=cmd_export)

    import_cmd = sub.add_parser("import", help="restore a JSON export")
    import_cmd.add_argument("file", help="Exportdatei (.json oder .json.gz)")
    import_cmd.set_defaults(func=cmd_import)

    seed = sub.add_parser("seed", help="fill the database with demo data")
    seed.add_argument("--days", type=int, default=30)
    seed.set_defaults(func=cmd_seed)

    login = sub.add_parser("login", help="log in once and store the tokens")
    login.set_defaults(func=cmd_login)

    token_info = sub.add_parser("token-info", help="show the lifetime of the stored tokens")
    token_info.set_defaults(func=cmd_token_info)

    whoami = sub.add_parser("whoami", help="show the user and account behind the stored token")
    whoami.set_defaults(func=cmd_whoami)

    diagnose = sub.add_parser("diagnose", help="check which data the account delivers")
    diagnose.add_argument("--out", default=None, help="write the report as JSON")
    diagnose.set_defaults(func=cmd_diagnose)

    init_db = sub.add_parser("init-db", help="create the database schema")
    init_db.set_defaults(func=cmd_init_db)

    reset = sub.add_parser("reset", help="delete all data (e.g. demo data)")
    reset.add_argument("--yes", action="store_true", help="bestätigen")
    reset.set_defaults(func=cmd_reset)

    jobs = sub.add_parser("jobs", help="show the job schedule")
    jobs.set_defaults(func=cmd_jobs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
