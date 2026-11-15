from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from oenergy.config import get_settings
from oenergy.db.base import get_database
from oenergy.jobs import start_scheduler, stop_scheduler
from oenergy.web.routes import router

logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"


def _check_token(database) -> None:
    """Warn when the refresh token is about to expire (a new login is needed)."""
    from oenergy.services.identity import token_status

    with database.session() as session:
        status = token_status(session)
    if not status.get("logged_in"):
        logger.warning("No login yet - run 'oenergy login' to connect to Octopus Energy")
    elif status.get("needs_login"):
        logger.error(
            "The refresh token expired on %s - please run 'oenergy login' (E-Mail and password needed)",
            status.get("refresh_expires_at"),
        )
    elif status.get("expires_soon"):
        logger.warning(
            "The refresh token expires on %s (in %s days) - after that 'oenergy login' is needed",
            status.get("refresh_expires_at"),
            status.get("refresh_days_left"),
        )


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = get_database()
        database.create_all()
        logger.info("Database ready at %s", database.url)
        _check_token(database)
        start_scheduler(settings)
        try:
            yield
        finally:
            stop_scheduler()

    app = FastAPI(
        title=settings.app_name,
        description="Dashboard für Octopus Energy Germany (Dynamic Octopus)",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app


app = create_app()
