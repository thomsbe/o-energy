"""Anmeldung mit Benutzername und Passwort.

Für den Betrieb als Einzelplatz-Anwendung genügt ein Benutzer.  Das Passwort
wird mit Argon2id gehasht (pwdlib), die Sitzung liegt in einem signierten
Cookie (Starlette SessionMiddleware).  Der Hash steht in der `.env`
(`AUTH_PASSWORD_HASH`), erzeugt mit `oenergy set-password`.

Ohne gesetzten Hash bleibt die Anmeldung deaktiviert - die App ist dann
ungegen, was auf einem öffentlich erreichbaren Server nicht gewollt ist.  Die
Oberfläche weist in diesem Fall darauf hin.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from pwdlib import PasswordHash
from starlette.datastructures import URL
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from oenergy.config import Settings

password_hash = PasswordHash.recommended()

#: Diese Pfade sind ohne Anmeldung erreichbar
PUBLIC_PATHS = ("/login", "/api/health", "/favicon.ico")

#: Die Passkey-Anmeldung muss ohne Session funktionieren - sonst liefert die
#: Middleware eine Umleitung auf das Login-Formular statt JSON.
PUBLIC_PREFIXES = ("/api/auth/passkey/login",)


def hash_password(password: str) -> str:
    """Argon2id-Hash für ein Passwort."""
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Prüft ein Passwort gegen den gespeicherten Hash."""
    if not hashed:
        return False
    try:
        return password_hash.verify(password, hashed)
    except Exception:  # pragma: no cover - ungültiger Hash
        return False


def auth_enabled(settings: Settings) -> bool:
    return bool(settings.auth_enabled and settings.auth_password_hash)


def is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/static"):
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


class RequireLoginMiddleware:
    """Schützt alle Routen ausser den öffentlichen."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not auth_enabled(self.settings):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if is_public_path(path):
            await self.app(scope, receive, send)
            return

        session = scope.get("session") or {}
        if session.get("user"):
            await self.app(scope, receive, send)
            return

        target = URL(scope=scope).path
        if scope.get("query_string"):
            target = target + "?" + scope["query_string"].decode()

        if path.startswith("/api/"):
            # API-Aufrufe bekommen JSON statt einer Umleitung auf die Login-Seite
            from fastapi.responses import JSONResponse

            response = JSONResponse({"error": "nicht angemeldet"}, status_code=401)
        else:
            response = RedirectResponse(url=f"/login?next={target}", status_code=303)
        await response(scope, receive, send)


def ensure_session_secret(settings: Settings, database) -> str:
    """Sorgt für ein stabiles Secret zum Signieren der Sitzungs-Cookies."""
    from sqlalchemy import select

    from oenergy.db.models import AppSetting

    if settings.auth_secret:
        return settings.auth_secret

    with database.session() as session:
        row = session.scalar(select(AppSetting).where(AppSetting.key == "session_secret"))
        if row is None:
            row = AppSetting(key="session_secret", value=secrets.token_urlsafe(48))
            session.add(row)
            session.commit()
        return row.value


def login(request: Request, password: str, settings: Settings) -> bool:
    """Prüft das Passwort und legt die Sitzung an.

    Es gibt nur einen Benutzer, deshalb wird kein Benutzername abgefragt.
    """
    if not auth_enabled(settings):
        return False
    if not verify_password(password, settings.auth_password_hash or ""):
        return False
    request.session["user"] = settings.auth_username
    return True


def logout(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> str | None:
    return (request.session or {}).get("user")


def redirect_to_login(next_url: str | None) -> Response:
    target = "/"
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        target = next_url
    return RedirectResponse(url=target, status_code=303)


def session_file_hint(data_dir: Path) -> str:
    """Hinweis, wo das Secret liegt (nur für die Dokumentation)."""
    return str(data_dir / "session-secret")
