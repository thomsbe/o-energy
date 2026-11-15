from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy.orm import Session

from oenergy.config import get_settings
from oenergy.db.base import get_database, get_session
from oenergy.db.models import Bill
from oenergy.services import dashboard
from oenergy.services.identity import token_status
from oenergy.services.sync import latest_runs, run_job, status_overview
from oenergy.utils import to_local

PACKAGE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PACKAGE_DIR / "web" / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def api_response(data: dict | list) -> JSONResponse:
    """JSON response that also serialises datetimes."""
    return JSONResponse(content=jsonable_encoder(data))


router = APIRouter()


def _tz():
    from zoneinfo import ZoneInfo

    return ZoneInfo(get_settings().timezone)


@router.get("/", response_class=HTMLResponse)
def page_dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "agreement": dashboard.agreement_info(session, get_settings()),
            "prices": dashboard.price_overview(session, get_settings()),
            "consumption": dashboard.consumption_summary(session, get_settings()),
            "costs": dashboard.cost_summary(session, get_settings()),
            "heatmap": dashboard.heatmap_data(session, get_settings(), days=7),
            "cheapest": dashboard.cheapest_windows(session, get_settings()),
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


@router.get("/verbrauch", response_class=HTMLResponse)
def page_consumption(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "consumption.html",
        {
            "active": "consumption",
            "agreement": dashboard.agreement_info(session, settings),
            "consumption": dashboard.consumption_summary(session, settings),
            "readings": dashboard.meter_readings(session, 60),
            "prices": dashboard.price_overview(session, settings),
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


@router.get("/preise", response_class=HTMLResponse)
def page_prices(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "prices.html",
        {
            "active": "prices",
            "agreement": dashboard.agreement_info(session, settings),
            "prices": dashboard.price_overview(session, settings),
            "heatmap": dashboard.heatmap_data(session, settings, days=7),
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


@router.get("/kosten", response_class=HTMLResponse)
def page_costs(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    bills = list(session.query(Bill).order_by(Bill.issued_at.desc().nullslast()).limit(24))
    return templates.TemplateResponse(
        request,
        "costs.html",
        {
            "active": "costs",
            "agreement": dashboard.agreement_info(session, settings),
            "costs": dashboard.cost_summary(session, settings),
            "bills": bills,
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


@router.get("/system", response_class=HTMLResponse)
def page_system(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    runs = latest_runs(session, 40)
    return templates.TemplateResponse(
        request,
        "system.html",
        {
            "active": "system",
            "runs": runs,
            "settings": settings,
            "has_credentials": settings.has_credentials,
            "backfill": dashboard.backfill_overview(session, settings),
            "token": token_status(session),
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


@router.get("/partials/tiles", response_class=HTMLResponse)
def partial_tiles(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = get_settings()
    status = status_overview(session)
    return templates.TemplateResponse(
        request,
        "partials/tiles.html",
        {
            "prices": dashboard.price_overview(session, settings),
            "consumption": dashboard.consumption_summary(session, settings),
            "costs": dashboard.cost_summary(session, settings),
            "last_success": status["last_success"],
            "job_status": status["jobs_list"],
            "now": to_local(datetime.utcnow(), _tz()),
        },
    )


@router.get("/partials/sync-status", response_class=HTMLResponse)
def partial_sync_status(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    runs = latest_runs(session, 5)
    return templates.TemplateResponse(request, "partials/sync_status.html", {"runs": runs})


@router.post("/actions/sync", response_class=HTMLResponse)
def action_sync(request: Request, job: str = "readings") -> HTMLResponse:
    logger.info("Manual sync requested for job {}", job)
    run = run_job(job)
    return templates.TemplateResponse(
        request,
        "partials/sync_status.html",
        {"runs": [run]},
    )


# ---------------------------------------------------------------------------
# JSON API for the charts
# ---------------------------------------------------------------------------


@router.get("/api/chart/prices")
def chart_prices(session: Session = Depends(get_session)) -> JSONResponse:
    return api_response(dashboard.price_chart_data(session, get_settings()))


@router.get("/api/chart/consumption")
def chart_consumption(session: Session = Depends(get_session)) -> JSONResponse:
    return api_response(dashboard.consumption_chart_data(session, get_settings()))


@router.get("/api/chart/costs")
def chart_costs(session: Session = Depends(get_session)) -> JSONResponse:
    return api_response(dashboard.cost_chart_data(session, get_settings()))


@router.get("/api/state")
def api_state(session: Session = Depends(get_session)) -> JSONResponse:
    settings = get_settings()
    return api_response(
        {
            "prices": dashboard.price_overview(session, settings),
            "consumption": dashboard.consumption_summary(session, settings),
            "costs": dashboard.cost_summary(session, settings),
            "agreement": dashboard.agreement_info(session, settings),
        }
    )


@router.get("/api/export/prices.csv")
def export_prices(session: Session = Depends(get_session)) -> Response:
    settings = get_settings()
    data = dashboard.price_chart_data(session, settings, hours=24 * 14)
    lines = ["start,end,net_ct_per_kwh,gross_ct_per_kwh"]
    for entry in data["customer"]:
        lines.append(f"{entry['start']},{entry['end']},{entry['net']},{entry['gross']}")
    body = "\n".join(lines)
    return Response(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=preise.csv"},
    )


@router.get("/api/health")
def health() -> JSONResponse:
    database = get_database()
    return JSONResponse({"status": "ok", "db": database.url})
