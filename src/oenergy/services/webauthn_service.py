"""WebAuthn (Passkeys) für den einzigen Benutzer.

Der Ablauf besteht aus zwei Zeremonien:

1. **Registrierung** (nur für angemeldete Benutzer): der Server erzeugt die
   Optionen, der Browser erzeugt den Schlüssel, der Server prüft die Antwort und
   legt den Schlüssel in der Datenbank ab.
2. **Anmeldung**: der Server erzeugt die Challenge, der Browser signiert sie mit
   dem Passkey, der Server prüft die Signatur und setzt die Sitzung.

Die Challenge wird jeweils in der Sitzung abgelegt (serverseitig, signiert).
"""

from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    options_to_json_dict,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from oenergy.config import Settings
from oenergy.db.models import WebauthnCredential

#: Feste Kennung des einzigen Benutzers (WebAuthn verlangt sie bei der Registrierung)
USER_ID = b"o-energy-single-user"
USER_NAME = "o-energy"
USER_DISPLAY_NAME = "o-energy"


@dataclass
class WebauthnContext:
    rp_id: str
    origin: str


def context_for(request_origin: str, settings: Settings) -> WebauthnContext:
    """RP-ID und Origin bestimmen (konfiguriert oder aus der Anfrage)."""
    rp_id = settings.webauthn_rp_id
    origin = settings.webauthn_origin
    if not rp_id or not origin:
        from urllib.parse import urlparse

        parsed = urlparse(request_origin)
        rp_id = rp_id or parsed.hostname or "localhost"
        origin = origin or request_origin.rstrip("/")
    return WebauthnContext(rp_id=rp_id, origin=origin.rstrip("/"))


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def list_credentials(session: Session) -> list[WebauthnCredential]:
    return list(session.scalars(select(WebauthnCredential).order_by(WebauthnCredential.created_at)))


def registration_options(
    session: Session, context: WebauthnContext, settings: Settings
) -> tuple[dict, bytes]:
    """Optionen für die Registrierung eines neuen Passkeys."""
    existing = list_credentials(session)
    options = generate_registration_options(
        rp_id=context.rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=USER_ID,
        user_name=USER_NAME,
        user_display_name=USER_DISPLAY_NAME,
        attestation=AttestationConveyancePreference.NONE,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64.urlsafe_b64decode(cred.credential_id + "=" * (-len(cred.credential_id) % 4))
            )
            for cred in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return options_to_json_dict(options), options.challenge


def finish_registration(
    session: Session,
    context: WebauthnContext,
    response: dict,
    expected_challenge: bytes,
    label: str | None = None,
) -> WebauthnCredential:
    """Die Antwort des Browsers prüfen und den Passkey speichern."""
    credential = parse_registration_credential_json(response)
    verification = verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=context.rp_id,
        expected_origin=context.origin,
    )

    credential_id = _encode(verification.credential_id)
    row = session.scalar(select(WebauthnCredential).where(WebauthnCredential.credential_id == credential_id))
    if row is None:
        row = WebauthnCredential(credential_id=credential_id)
        session.add(row)
    row.public_key = verification.credential_public_key
    row.sign_count = verification.sign_count
    row.aaguid = str(verification.aaguid) if verification.aaguid else None
    row.label = label or (response.get("label") or "Passkey")
    row.created_at = row.created_at or datetime.utcnow()
    session.commit()
    return row


def authentication_options(session: Session, context: WebauthnContext) -> tuple[dict, bytes]:
    """Optionen für die Anmeldung mit einem Passkey."""
    credentials = list_credentials(session)
    options = generate_authentication_options(
        rp_id=context.rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(
                id=base64.urlsafe_b64decode(cred.credential_id + "=" * (-len(cred.credential_id) % 4))
            )
            for cred in credentials
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json_dict(options), options.challenge


def finish_authentication(
    session: Session,
    context: WebauthnContext,
    response: dict,
    expected_challenge: bytes,
    require_user_verification: bool = True,
) -> WebauthnCredential:
    """Die Signatur prüfen und den Zählerstand aktualisieren."""
    credential = parse_authentication_credential_json(response)
    raw_id = credential.raw_id
    credential_id = _encode(raw_id)
    row = session.scalar(select(WebauthnCredential).where(WebauthnCredential.credential_id == credential_id))
    if row is None:
        raise ValueError("Unbekannter Passkey")

    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=context.rp_id,
        expected_origin=context.origin,
        credential_public_key=row.public_key,
        credential_current_sign_count=row.sign_count,
        require_user_verification=require_user_verification,
    )
    row.sign_count = verification.new_sign_count
    row.last_used_at = datetime.utcnow()
    session.commit()
    return row


def delete_credential(session: Session, credential_id: str) -> bool:
    row = session.scalar(select(WebauthnCredential).where(WebauthnCredential.credential_id == credential_id))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


#: Wie lange eine Challenge gültig ist (Sekunden)
CHALLENGE_TTL = 120


def new_challenge() -> bytes:
    return secrets.token_bytes(32)


def store_challenge(session_data: dict, challenge: bytes) -> None:
    """Challenge mit Zeitstempel in der Sitzung ablegen."""
    session_data["webauthn_challenge"] = challenge.hex()
    session_data["webauthn_challenge_at"] = time.time()


def load_challenge(session_data: dict) -> bytes | None:
    """Challenge aus der Sitzung lesen - nur, solange sie nicht abgelaufen ist."""
    value = session_data.get("webauthn_challenge")
    created = session_data.get("webauthn_challenge_at")
    if not value or created is None:
        return None
    if time.time() - float(created) > CHALLENGE_TTL:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None


def clear_challenge(session_data: dict) -> None:
    session_data.pop("webauthn_challenge", None)
    session_data.pop("webauthn_challenge_at", None)
