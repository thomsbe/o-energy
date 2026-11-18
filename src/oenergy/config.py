from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Runtime configuration, read from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Octopus Energy (Kraken) API -------------------------------------
    octopus_email: str | None = None
    octopus_password: str | None = None
    octopus_api_key: str | None = None
    api_url: str = "https://api.oeg-kraken.energy/v1/graphql/"
    api_timeout_seconds: float = 30.0
    api_max_retries: int = 6
    api_min_interval_seconds: float = 1.2  # the API rate limits the request rate

    # --- Application ------------------------------------------------------
    app_name: str = "o-energy"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    db_url: str = "sqlite:///./data/o-energy.db"
    timezone: str = "Europe/Berlin"

    # --- Anmeldung --------------------------------------------------------
    auth_enabled: bool = True
    auth_username: str = "admin"
    auth_password_hash: str | None = None
    auth_secret: str | None = None
    session_max_age_days: int = 14
    cookie_secure: bool = False  # auf True setzen, wenn die App nur über HTTPS erreichbar ist
    auth_local_bypass: bool = True  # localhost/127.0.0.1 ohne Anmeldung
    auth_bypass_token: str | None = None  # optionales Secret-Header (X-OEnergy-Token)

    # --- MCP (read-only access for AI agents) -----------------------------
    # Ohne gesetzten Token bleibt /mcp gesperrt (401).
    mcp_token: str | None = None
    mcp_rate_limit_per_minute: int = 120
    mcp_max_body_bytes: int = 256 * 1024

    # --- WebAuthn / Passkey -----------------------------------------------
    webauthn_enabled: bool = True
    webauthn_rp_id: str | None = None  # Hostname (Standard: der Host der Anfrage)
    webauthn_rp_name: str = "o-energy"
    webauthn_origin: str | None = None  # z.B. https://strom.example.de
    webauthn_require_user_verification: bool = True  # PIN/Biometrie bei jedem Login

    # --- Logging ----------------------------------------------------------
    log_level: str = "INFO"
    log_file: str | None = None
    log_rotation: str = "10 MB"
    log_retention: str = "14 days"

    # --- Polling ----------------------------------------------------------
    poll_interval_minutes: int = 15
    readings_backfill_days: int = 3
    readings_history_days: int = 30
    readings_history_max_days: int = 0  # 0 = so weit zurück wie Daten vorhanden sind
    readings_history_chunk_days: int = 30  # wie weit ein Lauf zurückgreift
    readings_max_window_days: int = 7
    gap_retry_minutes: int = 30  # wie lange ein Fenster nach einer Abfrage gesperrt bleibt
    price_backfill_hours: int = 72
    slot_minutes: int = 15
    slot_expected_tolerance: float = 0.9
    finalization_grace_minutes: int = 120
    scheduler_enabled: bool = True

    # --- Optional overrides for the account under observation -------------
    account_number: str | None = None
    melo_number: str | None = None
    meter_id: str | None = None
    meter_obis: str | None = None
    energy_unit: str = "auto"  # auto | kwh | wh

    # --- Preiseinordnung (Preis-Matrix) -----------------------------------
    price_cheap_limit: float = 26.0  # ct/kWh brutto - bis hierher "günstig"
    price_expensive_limit: float = 40.0  # ct/kWh brutto - ab hier "teuer"

    @field_validator("auth_password_hash", mode="before")
    @classmethod
    def _unescape_hash(cls, value: str | None) -> str | None:
        """Der Compose-escapte Hash ($$argon2id$$…) wird zurückübersetzt.

        Docker Compose ersetzt $$ durch $, im Direktbetrieb (uv run oenergy serve)
        bleibt der Wert unverändert - ohne diese Normalisierung wäre der Hash
        dort ungültig und jede Anmeldung würde fehlschlagen.
        """
        if isinstance(value, str) and "$$" in value:
            return value.replace("$$", "$")
        return value

    @property
    def has_credentials(self) -> bool:
        return bool(self.octopus_api_key or (self.octopus_email and self.octopus_password))

    @property
    def slots_per_day(self) -> int:
        return int(24 * 60 / self.slot_minutes)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_db_path(url: str) -> str:
    """Make sure the directory of a file based SQLite database exists."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = Path(url[len(prefix) :])
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
    return url
