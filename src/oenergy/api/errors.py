from __future__ import annotations

from typing import Any


class KrakenError(RuntimeError):
    """Raised when the Kraken API returns a GraphQL error payload."""

    def __init__(self, message: str, *, code: str | None = None, path: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.path = path or []

    @property
    def is_auth_error(self) -> bool:
        return self.code in {"KT-CT-1112", "KT-CT-1113", "KT-CT-1138", "KT-CT-1139"} or self.code in {
            "AUTHENTICATION_FAILED",
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> KrakenError:
        errors = payload.get("errors") or []
        if not errors:
            return cls("Unknown API error", code="UNKNOWN")
        first = errors[0]
        extensions = first.get("extensions") or {}
        return cls(
            message=extensions.get("errorDescription") or first.get("message") or "Unknown API error",
            code=extensions.get("errorCode"),
            path=first.get("path") or [],
        )


class KrakenAuthError(KrakenError):
    """Authentication against the Kraken API failed."""


class KrakenTransportError(KrakenError):
    """The API could not be reached (network problem, 5xx, ...)."""
