from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.sessions import SessionMiddleware

from oenergy import __version__
from oenergy.config import get_settings
from oenergy.db.base import get_database
from oenergy.jobs import start_scheduler, stop_scheduler
from oenergy.logging_config import setup_logging
from oenergy.web.auth import RequireLoginMiddleware, ensure_session_secret
from oenergy.web.routes import router

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
    setup_logging(
        level=settings.log_level,
        log_file=settings.log_file,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = get_database()
        database.create_all()
        logger.info("Database ready at {}", database.url)
        _check_token(database)
        start_scheduler(settings)
        try:
            yield
        finally:
            stop_scheduler()

    app = FastAPI(
        title=settings.app_name,
        description="Dashboard für Octopus Energy Germany (Dynamic Octopus)",
        version=__version__,
        license_info={"name": "MIT"},
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)

    # Anmeldung: die Session wird von aussen nach innen aufgebaut, damit die
    # Middleware die Session bereits vorfindet.
    app.add_middleware(RequireLoginMiddleware, settings=settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=ensure_session_secret(settings, get_database()),
        session_cookie="oenergy_session",
        max_age=settings.session_max_age_days * 24 * 3600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    return app


app = create_app()
