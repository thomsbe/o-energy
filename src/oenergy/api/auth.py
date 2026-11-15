from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from oenergy.api import queries
from oenergy.api.errors import KrakenAuthError, KrakenError
from oenergy.config import Settings
from oenergy.db.models import Token

logger = logging.getLogger(__name__)

PostCallable = Callable[[str, dict[str, Any], str | None], dict[str, Any]]


def _expires_at(refresh_expires_in: Any) -> datetime | None:
    """Kraken delivers the refresh lifetime in milliseconds."""
    try:
        return datetime.utcnow() + timedelta(milliseconds=int(refresh_expires_in))
    except (TypeError, ValueError):
        return None


def jwt_expiry(token: str | None) -> datetime | None:
    """Read the ``exp`` claim of a JWT (Kraken access tokens are JWTs)."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
        claims = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    return datetime.fromtimestamp(exp, tz=UTC).replace(tzinfo=None)


class TokenManager:
    """Keeps a valid Kraken access token in the database and renews it when needed."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        post: PostCallable,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._post = post

    # -- public API ------------------------------------------------------
    def access_token(self, *, force_refresh: bool = False) -> str:
        with self._session_factory() as session:
            row = session.get(Token, 1)
            token = row.access_token if row else None
            expires_at = row.expires_at if row else None
            refresh_token = row.refresh_token if row else None

            if not force_refresh and token:
                if expires_at is None or expires_at > datetime.utcnow() + timedelta(seconds=60):
                    return token
                logger.info("Kraken access token expired, renewing")
            elif force_refresh:
                logger.info("Forcing Kraken token renewal")

            if refresh_token:
                try:
                    return self._renew_with_refresh_token(session, refresh_token)
                except KrakenError as exc:
                    logger.warning("Refresh token failed (%s), falling back to full login", exc.message)

            return self._login(session)

    def full_login(self) -> str:
        """Always log in with email/password (ignores cached tokens)."""
        with self._session_factory() as session:
            return self._login(session)

    def invalidate(self) -> None:
        with self._session_factory() as session:
            row = session.get(Token, 1)
            if row:
                row.access_token = None
                row.expires_at = None
                row.updated_at = datetime.utcnow()
                session.commit()

    # -- internals -------------------------------------------------------
    def _login(self, session: Session) -> str:
        settings = self._settings
        if settings.octopus_api_key:
            payload = self._post(
                queries.OBTAIN_TOKEN,
                {"input": {"APIKey": settings.octopus_api_key}},
                None,
            )
        elif settings.octopus_email and settings.octopus_password:
            payload = self._post(
                queries.OBTAIN_TOKEN,
                {
                    "input": {
                        "email": settings.octopus_email,
                        "password": settings.octopus_password,
                    }
                },
                None,
            )
        else:
            raise KrakenAuthError(
                "Kein Login möglich: es sind weder Zugangsdaten noch ein gültiges "
                "Refresh-Token hinterlegt. Bitte 'oenergy login' ausführen "
                "(OCTOPUS_EMAIL/OCTOPUS_PASSWORD in der .env setzen).",
                code="NO_CREDENTIALS",
            )

        data = (payload.get("data") or {}).get("obtainKrakenToken") or {}
        token = data.get("token")
        if not token:
            raise KrakenAuthError("Kraken did not return a token", code="NO_TOKEN")

        self._store(session, token, data.get("refreshToken"), data.get("refreshExpiresIn"))
        self._ensure_long_lived_refresh_token(session, token)
        return token

    def _renew_with_refresh_token(self, session: Session, refresh_token: str) -> str:
        payload = self._post(
            queries.OBTAIN_TOKEN,
            {"input": {"refreshToken": refresh_token}},
            None,
        )
        data = (payload.get("data") or {}).get("obtainKrakenToken") or {}
        token = data.get("token")
        if not token:
            raise KrakenAuthError("Kraken did not return a token", code="NO_TOKEN")
        self._store(session, token, data.get("refreshToken") or refresh_token, data.get("refreshExpiresIn"))
        return token

    def _ensure_long_lived_refresh_token(self, session: Session, access_token: str) -> None:
        """Ask for a long lived refresh token.

        The mutation needs the access token in the Authorization header and is
        not available for customer accounts ("viewer is not authorized") -
        in that case the regular refresh token (about 20 days) is used.
        """
        row = session.get(Token, 1)
        if row and row.refresh_kind == "long_lived":
            return
        try:
            payload = self._post(
                queries.OBTAIN_LONG_LIVED_REFRESH_TOKEN,
                {"input": {"krakenToken": access_token}},
                access_token,
            )
            data = (payload.get("data") or {}).get("obtainLongLivedRefreshToken") or {}
            refresh_token = data.get("refreshToken")
            if refresh_token:
                row = session.get(Token, 1)
                if row:
                    row.refresh_token = refresh_token
                    expires_in = data.get("refreshExpiresIn")
                    if expires_in:
                        row.refresh_expires_at = _expires_at(expires_in)
                    row.refresh_kind = "long_lived"
                    row.updated_at = datetime.utcnow()
                    session.commit()
                logger.info("Long lived refresh token obtained")
        except KrakenError as exc:
            logger.info("Long lived refresh token not available: %s", exc.message)

    def _store(
        self,
        session: Session,
        token: str,
        refresh_token: str | None,
        refresh_expires_in: int | None = None,
    ) -> None:
        row = session.get(Token, 1)
        if row is None:
            row = Token(id=1)
            session.add(row)
        row.access_token = token
        row.expires_at = jwt_expiry(token)
        if refresh_token:
            row.refresh_token = refresh_token
            row.refresh_kind = "standard"
            if refresh_expires_in:
                row.refresh_expires_at = _expires_at(refresh_expires_in)
        row.updated_at = datetime.utcnow()
        session.commit()
