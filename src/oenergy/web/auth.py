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

import contextlib
import secrets
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse
from pwdlib import PasswordHash
from starlette.datastructures import URL, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from oenergy.config import Settings

password_hash = PasswordHash.recommended()

#: Diese Pfade sind ohne Anmeldung erreichbar - der Service Worker muss
#: öffentlich sein, weil der Browser ihn ohne Sitzung lädt.
PUBLIC_PATHS = ("/login", "/api/health", "/sw.js")

#: Die Passkey-Anmeldung muss ohne Session funktionieren - sonst liefert die
#: Middleware eine Umleitung auf das Login-Formular statt JSON.
PUBLIC_PREFIXES = ("/api/auth/passkey/login",)

#: Hostnamen, die auf den lokalen Rechner zeigen
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


#: Epoche der Sitzungen - sie wird bei jedem Logout erhöht und macht ältere
#: Cookies (die kopiert worden sein könnten) wertlos.
_session_epoch = 0


def _current_epoch() -> int:
    global _session_epoch
    return _session_epoch


def bump_session_epoch() -> None:
    """Alle bisherigen Sitzungen entwerten."""
    global _session_epoch
    _session_epoch += 1


def _collect_headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """Header einsammeln - mehrfach vorhandene Header werden verworfen.

    Ein doppelt gesetzter Header (z. B. ein gefälschter Bypass-Token) ist ein
    Anzeichen für einen Angriff und darf nicht verwendet werden.
    """
    headers: dict[str, str] = {}
    for key, value in raw:
        name = key.decode().lower()
        if name in headers:
            headers[name] = ""
            continue
        headers[name] = value.decode()
    return headers


def is_local_request(scope: dict, host_header: str | None) -> bool:
    """True, wenn die Anfrage direkt an den lokalen Rechner ging.

    Es werden **beide** Bedingungen geprüft: der Host-Header *und* die
    Peer-Adresse. Der alleinige Check der Peer-Adresse wäre unsicher, weil ein
    Reverse-Proxy oder ein Tunnel ebenfalls von 127.0.0.1 kommt.
    """
    host = (host_header or "").split(":")[0].strip().lower()
    if host.startswith("::ffff:"):
        host = host[7:]
    if host not in LOCAL_HOSTS:
        return False
    client = scope.get("client")
    peer = client[0] if client else ""
    if peer.startswith("::ffff:"):
        # IPv4-mapped IPv6-Adresse (uvicorn auf ::)
        peer = peer[7:]
    return peer in ("127.0.0.1", "::1", "localhost")


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


class SecurityHeadersMiddleware:
    """Setzt Sicherheits- und Cache-Header für alle Antworten.

    * authentifizierte Seiten: ``Cache-Control: no-store`` (weder im Browser-
      noch im CDN-Cache halten)
    * ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``,
      ``Referrer-Policy``, ``Permissions-Policy`` und eine CSP
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        session = scope.get("session") or {}
        authenticated = bool(session.get("user")) or bool(scope.get("oenergy_auth"))

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
                headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; "
                    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                    "form-action 'self'",
                )
                if authenticated:
                    headers["Cache-Control"] = "no-store"
                    headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodyLimitMiddleware:
    """Begrenzt die Größe des Request-Bodys.

    Ohne Limit kann ein anonymer Angreifer mit einem einzelnen großen Body
    den Speicher des kleinen Heimservers belasten.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 256 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        length = headers.get("content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            from fastapi.responses import JSONResponse

            response = JSONResponse({"error": "Anfrage zu groß"}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    from fastapi.responses import JSONResponse

                    response = JSONResponse({"error": "Anfrage zu groß"}, status_code=413)
                    await response(scope, receive, send)
                    raise _BodyTooLarge
            return message

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, send)


class _BodyTooLarge(Exception):
    """Signal, dass die Anfrage während des Lesens zu groß wurde."""


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

        headers = _collect_headers(scope.get("headers", []))

        # lokale Zugriffe (Tests, curl auf localhost) ohne Anmeldung
        if self.settings.auth_local_bypass and is_local_request(scope, headers.get("host")):
            scope["oenergy_auth"] = {"user": self.settings.auth_username}
            await self.app(scope, receive, send)
            return

        # optionales Secret-Header für Skripte (zeitkonstanter Vergleich,
        # doppelte Header werden verworfen)
        token = self.settings.auth_bypass_token
        provided = headers.get("x-oenergy-token")
        if token and provided and secrets.compare_digest(token, provided):
            scope["oenergy_auth"] = {"user": self.settings.auth_username}
            await self.app(scope, receive, send)
            return

        session = scope.get("session") or {}
        if session.get("user") and _is_valid_session(session):
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


#: Fehlgeschlagene Anmeldeversuche je Herkunft (Adresse, Port)
_login_failures: dict[str, list[float]] = {}
#: Nach so vielen Fehlversuchen wird für eine Weile gesperrt
LOGIN_MAX_FAILURES = 10
LOGIN_LOCKOUT_SECONDS = 300


def _client_key(request: Request) -> str:
    """Der Schlüssel für die Fehlversuchszählung.

    Nur die **Adresse** - der Port ändert sich bei jedem Verbindungsaufbau
    und würde den Zähler nie greifen lassen.
    """
    client = request.client
    return client.host if client else "unknown"


def login_is_locked(request: Request) -> bool:
    """Zu viele Fehlversuche von dieser Adresse?"""
    key = _client_key(request)
    now = time.monotonic()
    recent = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_LOCKOUT_SECONDS]
    if recent:
        _login_failures[key] = recent
    return len(recent) >= LOGIN_MAX_FAILURES


def _register_failure(request: Request) -> None:
    key = _client_key(request)
    now = time.monotonic()
    attempts = [stamp for stamp in _login_failures.get(key, []) if now - stamp < LOGIN_LOCKOUT_SECONDS]
    attempts.append(now)
    _login_failures[key] = attempts


def _clear_failures(request: Request) -> None:
    _login_failures.pop(_client_key(request), None)


def login(request: Request, password: str, settings: Settings) -> bool:
    """Prüft das Passwort und legt die Sitzung an.

    Es gibt nur einen Benutzer, deshalb wird kein Benutzername abgefragt.
    """
    if not auth_enabled(settings):
        return False
    if not verify_password(password, settings.auth_password_hash or ""):
        _register_failure(request)
        return False
    _clear_failures(request)
    request.session["user"] = settings.auth_username
    request.session["epoch"] = _current_epoch()
    return True


def logout(request: Request) -> None:
    """Beendet die Sitzung.

    Das Cookie ist selbsttragend - ein kopierter Cookie-Wert bliebe sonst bis
    zum Ablauf gültig. Deshalb wird zusätzlich eine serverseitige Epoche
    gesetzt: ab jetzt werden nur noch Sitzungen mit dem aktuellen Stand
    akzeptiert.
    """
    request.session.clear()
    request.session["epoch"] = _current_epoch()


def _is_valid_session(session: dict) -> bool:
    """Gilt die Sitzung noch?

    Nach einem Abmelden wird die Epoche erhöht - ältere Cookies (die kopiert
    worden sein könnten) werden dann nicht mehr akzeptiert.
    """
    epoch = session.get("epoch")
    if epoch is None:
        # eine Sitzung ohne Epoche stammt von vor dieser Funktion - sie wird
        # nur akzeptiert, solange noch nie abgemeldet wurde
        return _current_epoch() == 0
    return epoch == _current_epoch()


def current_user(request: Request) -> str | None:
    """Der angemeldete Benutzer - auch bei einem Bypass (lokal/Secret-Header)."""
    bypass = (getattr(request, "scope", None) or {}).get("oenergy_auth")
    if bypass:
        return bypass.get("user")
    session = request.session or {}
    if not _is_valid_session(session):
        return None
    return session.get("user")


def safe_next(next_url: str | None, default: str = "/") -> str:
    """Prüft ein Weiterleitungsziel.

    Nur **relative** Ziele innerhalb der App sind erlaubt: das Ziel muss mit
    einem einfachen Schrägstrich beginnen, darf nicht mit "//"
    (protokoll-relativ) oder "\\" (Backslash-Variante) beginnen und darf keine
    Steuerzeichen enthalten. Damit ist ein Redirect auf eine fremde Seite
    ausgeschlossen.
    """
    if not next_url:
        return default
    candidate = next_url.strip()
    if not candidate.startswith("/"):
        return default
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return default
    if any(ord(char) < 32 for char in candidate):
        return default
    return candidate


def redirect_to_login(next_url: str | None) -> Response:
    return RedirectResponse(url=safe_next(next_url), status_code=303)


def session_file_hint(data_dir: Path) -> str:
    """Hinweis, wo das Secret liegt (nur für die Dokumentation)."""
    return str(data_dir / "session-secret")
