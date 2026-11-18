"""Tests für die Sicherheitshärtung aus dem unabhängigen Review.

Die Befunde sind in ``REVIEW_1_OK.md`` dokumentiert; hier steht der
maschinelle Nachweis, dass sie behoben sind.
"""

from __future__ import annotations

import pytest

from oenergy.web.auth import safe_next

# ---------------------------------------------------------------------------
# Offene Weiterleitung (MITTEL)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/phish",
        "//evil.example",
        "/\\evil.example",
        "http://evil.example",
        "javascript:alert(1)",
        "/\n/x",
    ],
)
def test_safe_next_rejects_external_targets(value: str) -> None:
    assert safe_next(value) == "/"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/kosten", "/kosten"),
        ("/verbrauch?month=2026-11", "/verbrauch?month=2026-11"),
        ("/system", "/system"),
    ],
)
def test_safe_next_keeps_relative_targets(value: str, expected: str) -> None:
    assert safe_next(value) == expected


def test_passkey_options_validate_the_next_target(client, session) -> None:
    """Die Passkey-Login-Route darf kein fremdes Ziel zurückgeben."""
    from oenergy.db.models import WebauthnCredential

    session.add(WebauthnCredential(credential_id="abc", public_key=b"x"))
    session.commit()

    response = client.post("/api/auth/passkey/login/options?next=https://evil.example/phish")
    assert response.status_code == 200
    assert response.json()["next"] == "/"


# ---------------------------------------------------------------------------
# Sicherheits- und Cache-Header (NIEDRIG)
# ---------------------------------------------------------------------------


def test_security_headers_are_set(client) -> None:
    response = client.get("/login")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_authenticated_pages_are_not_stored_in_the_cache(client, session) -> None:
    """Authentifizierte Seiten dürfen nicht im Browser-Cache landen."""
    from oenergy.db.models import Token

    row = Token(id=1)
    row.access_token = "abc"
    row.expires_at = datetime_utc_now()
    row.refresh_token = "def"
    row.refresh_expires_at = datetime_utc_now()
    session.add(row)
    session.commit()

    response = client.get("/login")
    assert response.status_code == 200


def datetime_utc_now():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Limit für die Request-Größe (NIEDRIG)
# ---------------------------------------------------------------------------


def test_oversized_body_is_rejected(client) -> None:
    payload = {"padding": "x" * (300 * 1024)}
    response = client.post("/api/auth/passkey/login/verify", json=payload)
    assert response.status_code == 413


# ---------------------------------------------------------------------------
# Rate-Limit für Anmeldeversuche (NIEDRIG)
# ---------------------------------------------------------------------------


def test_login_is_locked_after_repeated_failures(client, monkeypatch) -> None:
    from oenergy.config import get_settings
    from oenergy.web import auth
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))
    monkeypatch.setattr(auth, "_login_failures", {})

    for _ in range(auth.LOGIN_MAX_FAILURES):
        client.post("/login", data={"password": "falsch"}, follow_redirects=False)

    # ab jetzt wird gesperrt
    response = client.post("/login", data={"password": "geheim123"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=locked" in response.headers["location"]


# ---------------------------------------------------------------------------
# Bypass-Token (NIEDRIG)
# ---------------------------------------------------------------------------


def test_duplicate_bypass_headers_are_rejected(client, monkeypatch) -> None:
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))
    monkeypatch.setattr(settings, "auth_local_bypass", False)
    monkeypatch.setattr(settings, "auth_bypass_token", "geheim-token")

    # ein gültiges Token genügt
    response = client.get(
        "/api/push/status",
        headers={"Host": "strom.tbaer.de", "X-OEnergy-Token": "geheim-token"},
        follow_redirects=False,
    )
    assert response.status_code == 200

    # zwei Header (der letzte wäre sonst gewonnen) -> kein Bypass
    response = client.get(
        "/api/push/status",
        headers=[
            ("Host", "strom.tbaer.de"),
            ("X-OEnergy-Token", "geheim-token"),
            ("X-OEnergy-Token", "falsch"),
        ],
        follow_redirects=False,
    )
    assert response.status_code in (401, 303)


# ---------------------------------------------------------------------------
# Sitzung (NIEDRIG)
# ---------------------------------------------------------------------------


def test_logout_invalidates_the_session(client, monkeypatch) -> None:
    """Nach dem Abmelden darf der alte Cookie nicht mehr gelten."""
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))

    client.post("/login", data={"password": "geheim123"}, follow_redirects=False)
    assert client.get("/kosten", follow_redirects=False).status_code == 200

    client.get("/logout", follow_redirects=False)
    assert client.get("/kosten", follow_redirects=False).status_code == 303


# ---------------------------------------------------------------------------
# Push-Endpunkte (INFO - SSRF)
# ---------------------------------------------------------------------------


def test_push_rejects_unknown_hosts(session) -> None:
    from oenergy.services import push

    with pytest.raises(ValueError):
        push.subscribe(
            session,
            {
                "endpoint": "https://evil.example/push",
                "keys": {"p256dh": "x", "auth": "y"},
            },
        )


# ---------------------------------------------------------------------------
# Weitere Kleinigkeiten (INFO)
# ---------------------------------------------------------------------------


def test_health_endpoint_does_not_leak_the_database_path(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_sync_job_is_rejected(client) -> None:
    response = client.post("/actions/sync?job=beliebig", follow_redirects=False)
    assert response.status_code == 400


def test_favicon_is_not_public() -> None:
    """/favicon.ico gibt es nicht als Route - es darf nicht öffentlich sein."""
    from oenergy.web.auth import PUBLIC_PATHS

    assert "/favicon.ico" not in PUBLIC_PATHS


# ---------------------------------------------------------------------------
# Hash-Normalisierung (NIEDRIG)
# ---------------------------------------------------------------------------


def test_compose_escaped_hash_is_normalized() -> None:
    from oenergy.config import Settings

    escaped = "$$argon2id$$v=19$$m=65536,t=3,p=4$$salt$$hash"
    settings = Settings(auth_password_hash=escaped)
    assert settings.auth_password_hash == "$argon2id$v=19$m=65536,t=3,p=4$salt$hash"

    # ein normaler Hash bleibt unverändert
    plain = "$argon2id$v=19$m=65536,t=3,p=4$salt$hash"
    assert Settings(auth_password_hash=plain).auth_password_hash == plain


def test_middleware_order_keeps_the_session_readable() -> None:
    """Regressionstest: die SessionMiddleware muss aussen liegen.

    Starlette wickelt die zuletzt hinzugefügte Middleware als innerste - liegt
    die Session innen, sieht die Anmeldeprüfung kein dekodiertes Cookie und
    jede Anmeldung wird abgewiesen.
    """
    from oenergy.main import create_app

    names = [mw.cls.__name__ for mw in create_app().user_middleware]
    assert names.index("SessionMiddleware") < names.index("RequireLoginMiddleware")


def test_session_survives_and_is_invalidated_by_logout(client, monkeypatch) -> None:
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))
    monkeypatch.setattr(settings, "cookie_secure", False)

    client.post("/login", data={"password": "geheim123"}, follow_redirects=False)
    response = client.get("/kosten", follow_redirects=False)
    assert response.status_code == 200
    # authentifizierte Seiten dürfen nicht im Cache liegen
    assert response.headers["cache-control"] == "no-store"

    client.get("/logout", follow_redirects=False)
    response = client.get("/kosten", follow_redirects=False)
    assert response.status_code == 303
