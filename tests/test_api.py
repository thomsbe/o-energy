from __future__ import annotations

import httpx
import respx

from oenergy.api.client import KrakenClient
from oenergy.config import Settings

ENDPOINT = "https://api.oeg-kraken.energy/v1/graphql/"

TOKEN_RESPONSE = {
    "data": {
        "obtainKrakenToken": {
            "token": "token-1",
            "refreshToken": "refresh-1",
            "refreshExpiresIn": 3600,
        }
    }
}

AUTH_ERROR = {
    "errors": [
        {
            "message": "'Authorization' header not provided.",
            "extensions": {"errorCode": "KT-CT-1112", "errorDescription": "expired"},
        }
    ]
}

VIEWER_RESPONSE = {"data": {"viewer": {"email": "demo@example.org"}}}


def _api_handler(request: httpx.Request) -> httpx.Response:
    """Answers like the real API: no long lived refresh token for customer accounts."""
    payload = request.content.decode()
    if "obtainKrakenToken" in payload:
        return httpx.Response(200, json=TOKEN_RESPONSE)
    if "obtainLongLivedRefreshToken" in payload:
        return httpx.Response(
            200,
            json={
                "errors": [
                    {
                        "message": "The viewer is not authorized to execute the query/mutation.",
                        "extensions": {"errorCode": "KT-CT-1115"},
                    }
                ]
            },
        )
    return httpx.Response(200, json=VIEWER_RESPONSE)


def _settings() -> Settings:
    return Settings(
        db_url="sqlite:///:memory:",
        octopus_email="demo@example.org",
        octopus_password="secret",
        api_url=ENDPOINT,
        api_min_interval_seconds=0.0,
    )


@respx.mock
def test_login_and_query_use_the_authorization_header(database) -> None:
    route = respx.post(ENDPOINT).mock(side_effect=_api_handler)

    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        payload = client.execute("{ viewer { email } }")

    assert payload["data"]["viewer"]["email"] == "demo@example.org"
    assert route.calls[0].request.headers.get("Authorization") is None
    assert any(call.request.headers.get("Authorization") == "token-1" for call in route.calls)


@respx.mock
def test_bearer_header_is_tried_when_the_raw_token_is_rejected(database) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.content.decode()
        if "obtainKrakenToken" in payload:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        if "obtainLongLivedRefreshToken" in payload:
            return _api_handler(request)
        if request.headers.get("Authorization", "").startswith("Bearer "):
            return httpx.Response(200, json=VIEWER_RESPONSE)
        return httpx.Response(200, json=AUTH_ERROR)

    route = respx.post(ENDPOINT).mock(side_effect=handler)

    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        payload = client.execute("{ viewer { email } }")

    assert payload["data"]["viewer"]["email"] == "demo@example.org"
    assert route.calls[-1].request.headers["Authorization"] == "Bearer token-1"


@respx.mock
def test_expired_token_triggers_a_refresh_and_retry(database) -> None:
    state = {"queries": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.content.decode()
        if "obtainKrakenToken" in payload:
            return httpx.Response(200, json=TOKEN_RESPONSE)
        state["queries"] += 1
        # the first two attempts are rejected, then the renewed token works
        if state["queries"] <= 2:
            return httpx.Response(200, json=AUTH_ERROR)
        return httpx.Response(200, json=VIEWER_RESPONSE)

    respx.post(ENDPOINT).mock(side_effect=handler)

    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        payload = client.execute("{ viewer { email } }")

    assert payload["data"]["viewer"]["email"] == "demo@example.org"
    assert state["queries"] == 3


@respx.mock
def test_transport_errors_are_retried(database) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="unavailable")
        return _api_handler(request)

    respx.post(ENDPOINT).mock(side_effect=handler)

    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        payload = client.execute("{ viewer { email } }")

    assert payload["data"]["viewer"]["email"] == "demo@example.org"


def _jwt(exp: int) -> str:
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "sub": "user"}).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


@respx.mock
def test_login_stores_tokens_and_reports_the_lifetime(database) -> None:
    from datetime import UTC, datetime, timedelta

    expires = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    token_response = {
        "data": {
            "obtainKrakenToken": {
                "token": _jwt(expires),
                "refreshToken": "refresh-1",
                "refreshExpiresIn": 30 * 24 * 3600,
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.content.decode()
        if "obtainLongLivedRefreshToken" in payload:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "obtainLongLivedRefreshToken": {
                            "refreshToken": "refresh-long",
                            "refreshExpiresIn": 365 * 24 * 3600 * 1000,
                        }
                    }
                },
            )
        return httpx.Response(200, json=token_response)

    respx.post(ENDPOINT).mock(side_effect=handler)

    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        info = client.login()

    assert info["logged_in"] is True
    assert info["has_refresh_token"] is True
    assert info["access_expires_at"] is not None
    assert info["refresh_expires_at"] is not None
    # the access token expires in about an hour
    delta = info["access_expires_at"] - datetime.utcnow()
    assert timedelta(minutes=50) < delta < timedelta(minutes=70)


@respx.mock
def test_token_info_without_login(database) -> None:
    with KrakenClient(_settings(), database.session, httpx.Client()) as client:
        info = client.token_info()

    assert info["logged_in"] is False
