"""Authentication and limits for the MCP endpoint.

The MCP path has its own bearer token (``MCP_TOKEN``) and is intentionally
independent of the web session and of ``X-OEnergy-Token``.  The middleware only
handles requests below ``/mcp``; everything else passes through untouched.
"""

from __future__ import annotations

import contextlib
import secrets
import time
from collections import deque

from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from oenergy.config import Settings

MCP_PREFIX = "/mcp"
RATE_WINDOW_SECONDS = 60.0


def is_mcp_path(path: str) -> bool:
    return path == MCP_PREFIX or path.startswith(f"{MCP_PREFIX}/")


def _forwarded_value(scope: Scope, name: str) -> str | None:
    """First value of a forwarded header, or None."""
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            text = value.decode("latin-1").split(",")[0].strip()
            return text or None
    return None


def _canonical_mcp_url(scope: Scope) -> str:
    """Absolute URL of ``/mcp`` in the scheme the client actually used.

    Behind a tunnel/Proxy the ASGI scheme stays ``http`` because uvicorn only
    trusts ``127.0.0.1``; the public scheme therefore comes from
    ``X-Forwarded-Proto``. An already secure connection is never downgraded.
    """
    scheme = scope.get("scheme", "http")
    if scheme != "https":
        scheme = _forwarded_value(scope, "x-forwarded-proto") or scheme
    host = _forwarded_value(scope, "x-forwarded-host") or _forwarded_value(scope, "host")
    root_path = scope.get("root_path", "").rstrip("/")
    path = f"{root_path}{MCP_PREFIX}"
    return f"{scheme}://{host}{path}" if host else path


class McpPathMiddleware:
    """Keep ``/mcp`` canonical: no trailing slash on the public endpoint.

    ``/mcp/`` is redirected with ``307`` (method and body survive), while
    ``/mcp`` is rewritten internally to ``/mcp/`` because Starlette's mount
    only serves the FastMCP app below the slash - otherwise the mount would
    redirect in the wrong direction.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == f"{MCP_PREFIX}/":
            location = _canonical_mcp_url(scope)
            query = scope.get("query_string", b"").decode("latin-1")
            if query:
                location = f"{location}?{query}"
            await RedirectResponse(location, status_code=307)(scope, receive, send)
            return

        if path == MCP_PREFIX:
            scope = dict(scope)
            scope["path"] = f"{MCP_PREFIX}/"
            scope["raw_path"] = f"{MCP_PREFIX}/".encode("latin-1")
        await self.app(scope, receive, send)


def _bearer_token(scope: Scope) -> str | None:
    """Exactly one ``Authorization: Bearer <token>`` header, otherwise None."""
    values = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
    if len(values) != 1:
        return None
    try:
        text = values[0].decode("latin-1")
    except UnicodeDecodeError:  # pragma: no cover - latin-1 always decodes
        return None
    scheme, separator, token = text.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class McpAuthMiddleware:
    """Bearer-Token, Rate-Limit und Body-Limit fuer ``/mcp``."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._requests: dict[str, deque[float]] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_mcp_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        token = self.settings.mcp_token
        provided = _bearer_token(scope)
        if not token or not provided:
            await self._deny(send, 401, "unauthorized", {"WWW-Authenticate": "Bearer"})
            return
        try:
            valid = secrets.compare_digest(token, provided)
        except TypeError:
            valid = False
        if not valid:
            await self._deny(send, 401, "unauthorized", {"WWW-Authenticate": "Bearer"})
            return

        limit = self.settings.mcp_rate_limit_per_minute
        if limit > 0 and not self._allow(scope, limit):
            await self._deny(send, 429, "rate limit exceeded", {"Retry-After": "60"})
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self.settings.mcp_max_body_bytes:
            await self._deny(send, 413, "request body too large")
            return

        received = 0
        max_bytes = self.settings.mcp_max_body_bytes

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    response = JSONResponse({"error": "request body too large"}, status_code=413)
                    await response(scope, receive, send)
                    raise _BodyTooLarge
            return message

        with contextlib.suppress(_BodyTooLarge):
            await self.app(scope, limited_receive, send)

    def _allow(self, scope: Scope, limit: int) -> bool:
        client = scope.get("client")
        key = client[0] if client else "unknown"
        now = time.monotonic()
        bucket = self._requests.setdefault(key, deque())
        while bucket and bucket[0] <= now - RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        if len(self._requests) > 512:
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        for key, bucket in list(self._requests.items()):
            while bucket and bucket[0] <= now - RATE_WINDOW_SECONDS:
                bucket.popleft()
            if not bucket:
                self._requests.pop(key, None)

    async def _deny(
        self, send: Send, status: int, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        response = JSONResponse({"error": detail}, status_code=status, headers=headers)
        await response({"type": "http"}, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class _BodyTooLarge(Exception):
    """Signal that the streaming body exceeded the limit."""
