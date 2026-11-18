"""Logging setup based on loguru.

All application logs go through loguru.  The standard library logging (uvicorn,
httpx, sqlalchemy, apscheduler, ...) is intercepted and forwarded, so everything
ends up in the same format - including the uvicorn access log.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import sys
from pathlib import Path

from loguru import logger

CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)

FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"


class InterceptHandler(logging.Handler):
    """Leitet Records der Standardbibliothek an loguru weiter."""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # bis zum Ursprung der Meldung zurücklaufen, damit der richtige
        # Dateiname und die Zeilennummer angezeigt werden
        frame, depth = inspect.currentframe(), 0
        while frame and (
            frame.f_code.co_filename == logging.__file__ or frame.f_code.co_filename == __file__
        ):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(
    level: str = "INFO",
    log_file: str | Path | None = None,
    rotation: str = "10 MB",
    retention: str = "14 days",
) -> None:
    """Konfiguriert Console- und optional Datei-Logging."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=CONSOLE_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # die Logdatei kann Token-Fehler enthalten - nur für den Eigentümer
        with contextlib.suppress(OSError):  # pragma: no cover
            path.parent.chmod(0o700)
        logger.add(
            str(path),
            level=level,
            format=FILE_FORMAT,
            rotation=rotation,
            retention=retention,
            compression="gz",
            enqueue=True,
            backtrace=False,
            diagnose=False,
        )
        with contextlib.suppress(OSError):  # pragma: no cover
            path.chmod(0o600)

    # Standardbibliothek-Logging auf loguru umbiegen
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    for name in (
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
        "sqlalchemy.engine",
        "apscheduler",
        "alembic",
    ):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False

    # HTTP-Protokoll der Client-Bibliothek ist zu gesprächig
    for name in ("httpx", "httpcore"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [InterceptHandler()]
        std_logger.propagate = False
        std_logger.setLevel(logging.WARNING)


__all__ = ["InterceptHandler", "logger", "setup_logging"]
