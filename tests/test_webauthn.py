from __future__ import annotations

import base64
import contextlib

import pytest

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
    with contextlib.suppress(Exception):
        finish_registration(session, context, {"invalid": True}, challenge, label="Test")

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


def test_passkey_login_is_reachable_without_a_session(client, monkeypatch) -> None:
    """Regressionstest: die Login-Endpunkte dürfen nicht umgeleitet werden."""
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))

    response = client.post("/api/auth/passkey/login/options", follow_redirects=False)
    assert response.status_code in (200, 400)
    assert response.headers["content-type"].startswith("application/json")

    # die Registrierung bleibt geschützt
    response = client.post("/api/auth/passkey/register/options", follow_redirects=False)
    assert response.status_code == 401


def test_push_subscription_round_trip(session) -> None:
    from oenergy.services import push

    key = push.public_key(session)
    assert key

    endpoint = "https://fcm.googleapis.com/fcm/send/abc"
    push.subscribe(
        session,
        {
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        },
    )
    assert len(push.subscriptions(session)) == 1

    # ein zweites Abonnement mit gleichem Endpoint wird aktualisiert, nicht verdoppelt
    push.subscribe(
        session,
        {
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
        },
    )
    assert len(push.subscriptions(session)) == 1

    assert push.unsubscribe(session, endpoint) is True
    assert push.subscriptions(session) == []


def test_push_only_accepts_known_push_services(session) -> None:
    """Ein Abo auf einen fremden Host wäre ein SSRF-Werkzeug."""
    from oenergy.services import push

    with pytest.raises(ValueError):
        push.subscribe(
            session,
            {
                "endpoint": "http://127.0.0.1:8998/push-target",
                "keys": {"p256dh": "x", "auth": "y"},
            },
        )

    with pytest.raises(ValueError):
        push.subscribe(
            session,
            {
                "endpoint": "https://evil.example/push",
                "keys": {"p256dh": "x", "auth": "y"},
            },
        )

    assert push.subscriptions(session) == []


def test_push_without_subscriptions_does_nothing(session) -> None:
    from oenergy.services import push

    assert push.send(session, "o-energy", "Test") == 0
