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


def test_consumption_page_supports_month_navigation(client, seeded) -> None:
    response = client.get("/verbrauch?month=2026-10")
    assert response.status_code == 200
    assert "10/2026" in response.text
    assert "chart-monthly" in response.text
    assert "chart-consumption-price" in response.text

    # Vormonat und Folgemonat werden verlinkt
    assert "month=2026-09" in response.text
    assert "month=2026-11" in response.text


def test_monthly_chart_endpoint(client, seeded) -> None:
    response = client.get("/api/chart/monthly?month=2026-10")
    assert response.status_code == 200
    data = response.json()
    assert data["month"] == "2026-10"
    assert len(data["days"]) == 31


def test_consumption_price_endpoint(client, seeded) -> None:
    response = client.get("/api/chart/consumption-price")
    assert response.status_code == 200
    data = response.json()
    assert len(data["labels"]) == len(data["kwh"]) == len(data["price"])


def test_export_endpoint_returns_a_gzip_download(client, seeded) -> None:
    import gzip
    import json

    response = client.get("/api/export/data.json.gz")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]

    payload = json.loads(gzip.decompress(response.content))
    assert payload["format"] == "o-energy-export"
    assert payload["format_version"] == 1
    assert payload["tables"]["intervals"]


def test_access_tile_reflects_the_token_state(client, session) -> None:
    from datetime import datetime, timedelta

    from oenergy.db.models import Token

    # noch kein Login
    response = client.get("/system")
    assert "nicht angemeldet" in response.text

    # Token und Refresh-Token vorhanden
    row = Token(id=1)
    row.access_token = "abc"
    row.expires_at = datetime.utcnow() + timedelta(hours=1)
    row.refresh_token = "def"
    row.refresh_expires_at = datetime.utcnow() + timedelta(days=20)
    session.add(row)
    session.commit()

    response = client.get("/system")
    assert "Refresh-Token aktiv" in response.text
    assert "fehlen" not in response.text

    # Refresh-Token abgelaufen
    row.refresh_expires_at = datetime.utcnow() - timedelta(days=1)
    session.commit()

    response = client.get("/system")
    assert "erneut anmelden" in response.text


def test_login_flow_with_password(client, monkeypatch) -> None:
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))

    # ohne Anmeldung wird auf die Login-Seite umgeleitet
    response = client.get("/kosten", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]

    # die Login-Seite ist erreichbar
    response = client.get("/login")
    assert response.status_code == 200
    assert "Anmeldung" in response.text

    # falsches Passwort
    response = client.post("/login", data={"username": "admin", "password": "falsch"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=1" in response.headers["location"]

    # richtiges Passwort
    response = client.post(
        "/login", data={"username": "admin", "password": "geheim123"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    # jetzt sind die Seiten erreichbar
    response = client.get("/kosten", follow_redirects=False)
    assert response.status_code == 200

    # Abmelden
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303
    response = client.get("/kosten", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_public_paths_stay_reachable(client, monkeypatch) -> None:
    from oenergy.config import get_settings
    from oenergy.web.auth import hash_password

    settings = get_settings()
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password_hash", hash_password("geheim123"))

    assert client.get("/api/health").status_code == 200
    assert client.get("/static/css/app.css").status_code == 200


def test_system_page_renders_the_passkey_script(client, seeded) -> None:
    """Regressionstest: In Jinja wird alles außerhalb von Blöcken nicht gerendert."""
    html = client.get("/system").text
    assert "passkey-register" in html
    assert "/api/auth/passkeys" in html
    assert "navigator.credentials.create" in html
