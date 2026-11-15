"""Who is logged in?

Shows the user, the accounts, the tariffs and whether the data is only demo
data - useful to avoid mixing up several accounts or to recognise a leftover
demo database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.db.models import Account, Agreement, Meter, Token

#: markers of the generated demo data (see services/demo.py)
DEMO_ACCOUNT = "A-DEMO1"
DEMO_METER = "demo-meter"
DEMO_EMAILS = ("demo@example.org",)


def is_demo(session: Session, email: str | None = None) -> bool:
    """True if the database only contains demo data."""
    if email and email.lower() in DEMO_EMAILS:
        return True
    account = session.scalar(select(Account).where(Account.number == DEMO_ACCOUNT))
    if account is not None:
        return True
    meter = session.scalar(select(Meter).where(Meter.id == DEMO_METER))
    return meter is not None


def token_status(session: Session) -> dict[str, Any]:
    """Lifetime of the stored tokens - the refresh token decides how often a
    new login is needed."""
    token = session.get(Token, 1)
    if token is None or not token.access_token:
        return {"logged_in": False, "state": "missing"}

    now = datetime.utcnow()
    refresh_left = None
    if token.refresh_expires_at:
        refresh_left = (token.refresh_expires_at - now).days
    needs_login = bool(refresh_left is not None and refresh_left <= 0)
    expires_soon = bool(refresh_left is not None and 0 < refresh_left <= 7)

    if needs_login:
        state = "renew"
    elif refresh_left is not None:
        state = "refresh"
    else:
        state = "token"

    return {
        "logged_in": True,
        "state": state,
        "access_expires_at": token.expires_at,
        "refresh_expires_at": token.refresh_expires_at,
        "refresh_days_left": refresh_left,
        "refresh_kind": token.refresh_kind,
        "needs_login": needs_login,
        "expires_soon": expires_soon,
    }


def identity(session: Session) -> dict[str, Any]:
    """The user and the accounts that belong to the stored token."""
    token = session.get(Token, 1)
    email = token.viewer_email if token else None
    name = token.viewer_name if token else None

    accounts = [
        {
            "number": row.number,
            "brand": row.brand,
            "status": row.status,
            "billing_name": row.billing_name,
            "balance_cents": row.balance_cents,
        }
        for row in session.scalars(select(Account).order_by(Account.number))
    ]
    agreements = [
        {
            "id": row.id,
            "product_code": row.product_code,
            "product_name": row.product_name,
            "is_time_of_use": row.is_time_of_use,
            "supply_point": row.supply_point_external_id,
        }
        for row in session.scalars(select(Agreement))
    ]
    meters = [
        {
            "id": row.id,
            "number": row.number,
            "melo_number": row.melo_number,
        }
        for row in session.scalars(select(Meter))
    ]

    return {
        "email": email,
        "name": name,
        "accounts": accounts,
        "agreements": agreements,
        "meters": meters,
        "is_demo": is_demo(session, email),
        "logged_in_at": token.updated_at if token else None,
        "known": bool(email or accounts),
    }


def format_identity(info: dict[str, Any]) -> str:
    lines: list[str] = []
    if info.get("name") or info.get("email"):
        who = info.get("name") or "unbekannter Name"
        lines.append(f"Angemeldet als: {who}")
        if info.get("email"):
            lines.append(f"  E-Mail      : {info['email']}")
    else:
        lines.append("Angemeldet als: unbekannt (noch kein Login durchgeführt)")

    for account in info.get("accounts", []):
        lines.append(
            f"Konto {account['number']} · {account['brand'] or 'unbekannt'} · "
            f"{account['status'] or '–'} · Saldo {(account['balance_cents'] or 0) / 100:.2f} €"
        )

    for agreement in info.get("agreements", []):
        lines.append(
            f"  Tarif {agreement['product_name'] or '–'} ({agreement['product_code'] or '–'}), "
            f"{'dynamisch' if agreement['is_time_of_use'] else 'klassisch'}, "
            f"SupplyPoint {agreement['supply_point'] or '–'}"
        )

    for meter in info.get("meters", []):
        lines.append(f"  Zähler {meter['number'] or meter['id']} (melo {meter['melo_number'] or '–'})")

    if info.get("is_demo"):
        lines.append("")
        lines.append("⚠ DEMO-DATEN - das ist kein echter Octopus-Zugang.")
        lines.append("  Zum Zurücksetzen: uv run oenergy reset --yes && uv run oenergy login")

    return "\n".join(lines)
