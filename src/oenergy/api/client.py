from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
from loguru import logger

from oenergy import __version__
from oenergy.api.auth import TokenManager
from oenergy.api.errors import KrakenError, KrakenTransportError
from oenergy.config import Settings, get_settings
from oenergy.db.base import get_database
from oenergy.db.models import Token


class KrakenClient:
    """Thin, retrying GraphQL client for the Octopus Energy Germany API."""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: Callable[[], Any] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory or (lambda: get_database().session())
        self._http = http_client or httpx.Client(
            timeout=self._settings.api_timeout_seconds,
            headers={"User-Agent": f"{self._settings.app_name}/{__version__} (+https://octopusenergy.de)"},
        )
        self._owns_http = http_client is None
        self._auth_style = "raw"
        self._last_request = 0.0
        self._throttle_seconds = settings.api_min_interval_seconds
        self._tokens = TokenManager(self._settings, self._session_factory, self._raw_post)

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> KrakenClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _throttle(self) -> None:
        """Keep a minimum distance between two API requests."""
        interval = max(self._settings.api_min_interval_seconds, self._throttle_seconds)
        if interval <= 0:
            return
        wait = interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    # -- low level -------------------------------------------------------
    def _raw_post(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        token: str | None = None,
        *,
        auth_style: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = self._auth_header(token, auth_style)

        body = {"query": query}
        if variables:
            body["variables"] = variables

        self._throttle()
        last_error: Exception | None = None
        for attempt in range(self._settings.api_max_retries):
            try:
                response = self._http.post(self._settings.api_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("API request failed (attempt {}): {}", attempt + 1, exc)
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code >= 500 or response.status_code == 429:
                last_error = KrakenTransportError(
                    f"API returned HTTP {response.status_code}",
                    code=f"HTTP_{response.status_code}",
                )
                logger.warning("API returned HTTP {}, retrying", response.status_code)
                time.sleep(min(2**attempt, 8))
                continue

            if response.status_code >= 400:
                raise KrakenTransportError(
                    f"API returned HTTP {response.status_code}: {response.text[:200]}",
                    code=f"HTTP_{response.status_code}",
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise KrakenTransportError(f"Invalid JSON response: {exc}", code="INVALID_JSON") from exc

            if payload.get("errors"):
                error = KrakenError.from_payload(payload)
                message = error.message.lower()
                if "too many requests" in message or "internal error" in message:
                    last_error = error
                    # the API limits the request rate - wait and try again
                    wait = min(60, 20 * (attempt + 1))
                    logger.warning("API busy ({}), waiting {} seconds", error.message[:60], wait)
                    time.sleep(wait)
                    self._throttle_seconds = min(5.0, self._throttle_seconds * 2)
                    continue
                raise error
            return payload

        raise KrakenTransportError(
            f"API unreachable after {self._settings.api_max_retries} attempts: {last_error}"
        )

    # -- public API ------------------------------------------------------
    def _auth_header(self, token: str, auth_style: str | None = None) -> str:
        """Kraken expects the raw JWT - but some deployments want a Bearer prefix."""
        style = auth_style or self._auth_style
        return f"Bearer {token}" if style == "bearer" else token

    def login(self) -> dict[str, Any]:
        """Log in with the configured credentials and report the token lifetimes."""
        self._tokens.full_login()
        return self.token_info()

    def token_info(self) -> dict[str, Any]:
        """Information about the stored tokens (without logging in)."""
        with self._session_factory() as session:
            row = session.get(Token, 1)
            if row is None:
                return {"logged_in": False}
            return {
                "logged_in": bool(row.access_token),
                "access_expires_at": row.expires_at,
                "refresh_expires_at": row.refresh_expires_at,
                "refresh_kind": row.refresh_kind,
                "has_refresh_token": bool(row.refresh_token),
                "viewer_email": row.viewer_email,
                "viewer_name": row.viewer_name,
                "updated_at": row.updated_at,
            }

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        if not authenticated:
            return self._raw_post(query, variables, None)

        token = self._tokens.access_token()
        try:
            return self._raw_post(query, variables, token)
        except KrakenError as exc:
            if not exc.is_auth_error:
                raise

            # the token may be fine but the header format wrong -> try the other one
            other_style = "bearer" if self._auth_style == "raw" else "raw"
            try:
                result = self._raw_post(query, variables, token, auth_style=other_style)
            except KrakenError:
                result = None
            if result is not None:
                logger.info("Using '{}' authorization header style", other_style)
                self._auth_style = other_style
                return result

            logger.info("Kraken token rejected ({}), renewing and retrying", exc.code)
            token = self._tokens.access_token(force_refresh=True)
            return self._raw_post(query, variables, token)

    def paginate(
        self,
        query: str,
        variables: dict[str, Any],
        edges_path: tuple[str, ...],
        node_path: tuple[str, ...] = (),
        *,
        page_size: int = 100,
        max_pages: int = 40,
    ) -> Iterator[dict[str, Any]]:
        """Iterate over every edge of a Relay style connection."""
        after: str | None = None
        for _ in range(max_pages):
            variables = {**variables, "first": page_size}
            if after:
                variables["after"] = after
            payload = self.execute(query, variables)
            connection = payload.get("data") or {}
            for key in edges_path:
                connection = (connection or {}).get(key) or {}
            edges = (connection or {}).get("edges") or []
            for edge in edges:
                node = edge.get("node") or {}
                for key in node_path:
                    node = (node or {}).get(key) or {}
                yield node
            page_info = (connection or {}).get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")
            if not after:
                break
