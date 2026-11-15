from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    # --- Polling ----------------------------------------------------------
    poll_interval_minutes: int = 15
    readings_backfill_days: int = 3
    readings_history_days: int = 30
    readings_max_window_days: int = 7
    gap_retry_minutes: int = 60
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
