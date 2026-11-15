"""Web-Push-Benachrichtigungen.

Der Browser abonniert den Push-Dienst (FCM/Mozilla/WAP) und schickt das
Abonnement an den Server.  Meldet der Server neue Daten zu einem Tag, schickt
er eine Push-Nachricht an alle Abonnements:

    Neue Daten vom 14.11.: 8,5 kWh für 4,40 €

Die VAPID-Schlüssel (ein Schlüsselpaar für die Identität des Servers) werden
beim ersten Bedarf erzeugt und in der Datenbank abgelegt.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from oenergy.db.models import AppSetting, PushSubscription

KEY_PRIVATE = "vapid_private_key"
KEY_PUBLIC = "vapid_public_key"
KEY_CONTACT = "vapid_contact"

DEFAULT_CONTACT = "mailto:admin@example.org"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_keys() -> tuple[str, str]:
    """Erzeugt ein neues Schlüsselpaar.

    Der private Schlüssel wird als base64url (32 Byte roh) gespeichert - so
    akzeptiert ihn pywebpush direkt. Der öffentliche Schlüssel ist der
    unkomprimierte Punkt (65 Byte), ebenfalls base64url - so braucht der
    Browser ihn für applicationServerKey.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = _b64url(private_key.private_numbers().private_value.to_bytes(32, "big"))
    public_numbers = private_key.public_key().public_numbers()
    point = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    return private_raw, _b64url(point)


def _normalize_private(value: str) -> str:
    """Wandelt einen PEM-Schlüssel (ältere Version) in das rohe Format um."""
    if "-----BEGIN" not in value:
        return value
    key = serialization.load_pem_private_key(value.encode(), password=None)
    return _b64url(key.private_numbers().private_value.to_bytes(32, "big"))


def keys(session: Session) -> tuple[str, str]:
    """Das gespeicherte Schlüsselpaar (wird bei Bedarf erzeugt)."""
    private_row = session.get(AppSetting, KEY_PRIVATE)
    public_row = session.get(AppSetting, KEY_PUBLIC)
    if private_row and public_row:
        normalized = _normalize_private(private_row.value)
        if normalized != private_row.value:
            private_row.value = normalized
            session.commit()
        return normalized, public_row.value

    private_pem, public_key = generate_keys()
    for key, value in ((KEY_PRIVATE, private_pem), (KEY_PUBLIC, public_key)):
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            session.add(row)
        else:
            row.value = value
    session.commit()
    logger.info("Neue VAPID-Schlüssel erzeugt")
    return private_pem, public_key


def public_key(session: Session) -> str:
    return keys(session)[1]


def contact(session: Session) -> str:
    row = session.get(AppSetting, KEY_CONTACT)
    if row is None:
        row = AppSetting(key=KEY_CONTACT, value=DEFAULT_CONTACT)
        session.add(row)
        session.commit()
    return row.value


def subscribe(session: Session, payload: dict) -> PushSubscription:
    """Legt ein Abonnement an (oder aktualisiert es)."""
    endpoint = payload.get("endpoint")
    keys_data = payload.get("keys") or {}
    if not endpoint or not keys_data.get("p256dh") or not keys_data.get("auth"):
        raise ValueError("Unvollständiges Push-Abonnement")

    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        row = PushSubscription(endpoint=endpoint)
        session.add(row)
    row.p256dh = keys_data["p256dh"]
    row.auth = keys_data["auth"]
    row.failures = 0
    row.created_at = row.created_at or datetime.utcnow()
    session.commit()
    logger.info("Push-Abonnement hinzugefügt: {}", endpoint[:60])
    return row


def unsubscribe(session: Session, endpoint: str) -> bool:
    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def subscriptions(session: Session) -> list[PushSubscription]:
    return list(session.scalars(select(PushSubscription).order_by(PushSubscription.created_at)))


def send(session: Session, title: str, body: str, url: str = "/") -> int:
    """Schickt eine Push-Nachricht an alle Abonnements.

    Gibt die Anzahl der erfolgreichen Zustellungen zurück.
    """
    from pywebpush import WebPushException, webpush

    rows = subscriptions(session)
    if not rows:
        logger.info("Keine Push-Abonnements - keine Nachricht verschickt")
        return 0

    private_key, _ = keys(session)
    message = json.dumps({"title": title, "body": body, "url": url})
    delivered = 0

    for row in rows:
        subscription = {
            "endpoint": row.endpoint,
            "keys": {"p256dh": row.p256dh, "auth": row.auth},
        }
        # pywebpush trägt das "aud" (der Origin des Endpoints) in das Claims-Dict
        # ein - deshalb braucht jedes Abonnement einen eigenen Satz.
        claims = {"sub": contact(session)}
        try:
            webpush(
                subscription_info=subscription,
                data=message,
                vapid_private_key=private_key,
                vapid_claims=claims,
                ttl=3600,
            )
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                # das Abonnement ist ungültig - aufräumen
                session.delete(row)
                logger.info("Push-Abonnement entfernt ({}): {}", status, row.endpoint[:60])
            else:
                row.failures = (row.failures or 0) + 1
                logger.warning("Push-Nachricht fehlgeschlagen: {}", exc)
        except Exception as exc:
            row.failures = (row.failures or 0) + 1
            logger.warning("Push-Nachricht fehlgeschlagen: {}", exc)
        else:
            row.last_success_at = datetime.utcnow()
            row.failures = 0
            delivered += 1

    session.commit()
    logger.info("Push-Nachricht an {} von {} Abonnements zugestellt", delivered, len(rows))
    return delivered
