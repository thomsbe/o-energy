from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

fastmcp = pytest.importorskip("fastmcp")

from fastmcp import Client  # noqa: E402

from oenergy.config import get_settings  # noqa: E402


def _app_client(monkeypatch, token: str, *, auth_enabled: bool = False) -> TestClient:
    from oenergy.main import create_app
    from oenergy.web.auth import hash_password

    monkeypatch.setenv("MCP_TOKEN", token)
    monkeypatch.setenv("AUTH_ENABLED", "true" if auth_enabled else "false")
    if auth_enabled:
        monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("test-password"))
    get_settings.cache_clear()
    return TestClient(create_app())


MCP_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}


def test_mcp_is_closed_without_token(monkeypatch, database) -> None:
    monkeypatch.setenv("MCP_TOKEN", "")
    get_settings.cache_clear()
    from oenergy.main import create_app

    with TestClient(create_app()) as client:
        response = client.post("/mcp", json=MCP_REQUEST)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_mcp_rejects_missing_and_wrong_token(monkeypatch, database) -> None:
    with _app_client(monkeypatch, "secret-token") as client:
        missing = client.post("/mcp", json=MCP_REQUEST)
        wrong = client.post("/mcp", json=MCP_REQUEST, headers={"Authorization": "Bearer wrong"})
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_mcp_token_bypasses_the_web_login(monkeypatch, database) -> None:
    with _app_client(monkeypatch, "secret-token", auth_enabled=True) as client:
        web = client.get("/", follow_redirects=False)
        mcp = client.get("/mcp", headers={"Authorization": "Bearer secret-token"}, follow_redirects=False)

    assert web.status_code == 303
    assert web.headers["location"].startswith("/login")
    assert mcp.status_code != 401
    assert mcp.status_code != 303


def test_mcp_rate_limit(monkeypatch, database) -> None:
    with _app_client(monkeypatch, "secret-token") as client:
        get_settings().mcp_rate_limit_per_minute = 2
        headers = {"Authorization": "Bearer secret-token"}
        first = client.get("/mcp", headers=headers, follow_redirects=False)
        second = client.get("/mcp", headers=headers, follow_redirects=False)
        third = client.get("/mcp", headers=headers, follow_redirects=False)

    assert first.status_code != 429
    assert second.status_code != 429
    assert third.status_code == 429


def test_mcp_body_limit(monkeypatch, database) -> None:
    with _app_client(monkeypatch, "secret-token") as client:
        get_settings().mcp_max_body_bytes = 64
        response = client.post(
            "/mcp",
            content=b"x" * 65,
            headers={
                "Authorization": "Bearer secret-token",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 413


def test_mcp_http_initialize(monkeypatch, database) -> None:
    headers = {
        "Authorization": "Bearer secret-token",
        "Accept": "application/json, text/event-stream",
    }
    with _app_client(monkeypatch, "secret-token") as client:
        response = client.post("/mcp", json=MCP_REQUEST, headers=headers)

    assert response.status_code == 200
    assert '"serverInfo"' in response.text
    assert '"o-energy"' in response.text


def test_mcp_trailing_slash_redirects_to_canonical_url(monkeypatch, database) -> None:
    headers = {"Authorization": "Bearer secret-token"}
    with _app_client(monkeypatch, "secret-token") as client:
        plain = client.post("/mcp/?x=1", json=MCP_REQUEST, headers=headers, follow_redirects=False)
        proxied = client.post(
            "/mcp/",
            json=MCP_REQUEST,
            headers={
                **headers,
                "X-Forwarded-Proto": "https",
                "X-Forwarded-Host": "strom.example.de",
            },
            follow_redirects=False,
        )

    assert plain.status_code == 307
    assert plain.headers["location"] == "http://testserver/mcp?x=1"
    assert proxied.status_code == 307
    assert proxied.headers["location"] == "https://strom.example.de/mcp"


def test_server_exposes_tools_and_resources(seeded, database) -> None:
    from oenergy.mcp_server import build_server

    server = build_server(get_settings())

    async def exercise():
        async with Client(server) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            calls = {
                "status": {},
                "consumption": {"days": 3},
                "costs": {"days": 3},
                "prices": {"hours": 24},
                "cheapest_windows": {"days": 2, "count": 3},
                "price_at": {"when": "2026-11-17T12:00:00"},
                "monthly_overview": {},
                "account": {},
            }
            results = {name: await client.call_tool(name, arguments) for name, arguments in calls.items()}
            resource = await client.read_resource("energy://status")
            return tools, resources, results, resource

    tools, resources, results, resource = asyncio.run(exercise())

    assert {tool.name for tool in tools} == {
        "status",
        "consumption",
        "costs",
        "prices",
        "cheapest_windows",
        "price_at",
        "monthly_overview",
        "account",
    }
    assert {str(item.uri) for item in resources} == {
        "energy://status",
        "energy://today",
        "energy://month",
    }
    assert all(result.is_error is False for result in results.values())
    assert "generated_at" in results["status"].data
    assert results["consumption"].data["total_kwh"] > 0
    assert results["account"].data["tariff"]["name"] == "Dynamic Octopus"
    assert resource
