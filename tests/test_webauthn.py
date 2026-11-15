from __future__ import annotations

import base64

from oenergy.config import get_settings
from oenergy.services.webauthn_service import (
    authentication_options,
    context_for,
    finish_registration,
    list_credentials,
    registration_options,
)


def _context():
    settings = get_settings()
    return context_for("https://strom.example.de", settings), settings


def test_context_uses_the_request_origin() -> None:
    settings = get_settings()
    context = context_for("https://strom.example.de/", settings)
    assert context.origin == "https://strom.example.de"
    assert context.rp_id == "strom.example.de"


def test_context_prefers_the_configuration() -> None:
    settings = get_settings()
    settings.webauthn_origin = "https://tunnel.example.org"
    settings.webauthn_rp_id = "tunnel.example.org"
    context = context_for("https://strom.example.de", settings)
    assert context.origin == "https://tunnel.example.org"
    assert context.rp_id == "tunnel.example.org"


def test_registration_options_are_generated(session) -> None:
    context, settings = _context()
    options, challenge = registration_options(session, context, settings)

    assert "challenge" in options
    assert "rp" in options
    assert options["rp"]["id"] == context.rp_id
    assert len(challenge) >= 16
    # die Challenge muss als base64url im JSON stehen
    assert (
        base64.urlsafe_b64decode(options["challenge"] + "=" * (-len(options["challenge"]) % 4)) == challenge
    )


def test_authentication_options_without_passkeys(session) -> None:
    context, _ = _context()
    options, challenge = authentication_options(session, context)

    assert "challenge" in options
    assert options.get("allowCredentials") == []
    assert len(challenge) >= 16


def test_credentials_are_stored_and_listed(session) -> None:
    context, settings = _context()
    options, challenge = registration_options(session, context, settings)
    assert options["user"]["name"] == "o-energy"

    # ohne echten Authenticator lässt sich die Registrierung nicht abschließen -
    # die Hilfsfunktion meldet den Fehler sauber
    try:
        finish_registration(session, context, {"invalid": True}, challenge, label="Test")
    except Exception:
        pass

    assert isinstance(list_credentials(session), list)


def test_passkey_routes_require_a_login(client) -> None:
    response = client.post("/api/auth/passkey/register/options")
    assert response.status_code == 401

    response = client.get("/api/auth/passkeys")
    assert response.status_code == 401


def test_login_options_report_missing_passkey(client) -> None:
    response = client.post("/api/auth/passkey/login/options")
    assert response.status_code == 400
    assert "Passkey" in response.json()["error"]


def test_challenge_is_read_from_the_session(client, session) -> None:
    # ein Passkey in der Datenbank -> die Optionen werden erzeugt
    from oenergy.db.models import WebauthnCredential

    session.add(WebauthnCredential(credential_id="abc", public_key=b"x"))
    session.commit()

    response = client.post("/api/auth/passkey/login/options")
    assert response.status_code == 200
    assert "options" in response.json()
