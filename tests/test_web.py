from __future__ import annotations


def test_dashboard_renders(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Aktueller Preis" in response.text


def test_all_pages_render(client) -> None:
    for path in ("/", "/verbrauch", "/preise", "/kosten", "/system"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_health_endpoint(client) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chart_endpoints_return_json(client) -> None:
    for path in ("/api/chart/prices", "/api/chart/consumption", "/api/chart/costs"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("application/json")


def test_partial_tiles_are_htmx_enabled(client) -> None:
    response = client.get("/partials/tiles")
    assert response.status_code == 200
    assert "hx-get" in response.text
    assert "hx-trigger" in response.text


def test_seeded_dashboard_shows_data(client, seeded) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Dynamic Octopus" in response.text
    assert "ct/kWh" in response.text


def test_dashboard_shows_a_hint_without_data(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Noch keine Daten" in response.text
    assert "oenergy sync all" in response.text
