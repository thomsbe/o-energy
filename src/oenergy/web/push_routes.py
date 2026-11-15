"""Routen für die Web-Push-Benachrichtigungen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from oenergy.db.base import get_session
from oenergy.services import push
from oenergy.web.auth import current_user

router = APIRouter()


@router.get("/api/push/key")
async def push_key(request: Request, session: Session = Depends(get_session)):
    """Der öffentliche VAPID-Schlüssel für das Abonnieren im Browser."""
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    return JSONResponse({"key": push.public_key(session)})


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    try:
        payload = await request.json()
        push.subscribe(session, payload)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "count": len(push.subscriptions(session))})


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    payload = await request.json()
    removed = push.unsubscribe(session, payload.get("endpoint", ""))
    return JSONResponse({"ok": removed})


@router.get("/api/push/status")
async def push_status(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    rows = push.subscriptions(session)
    return JSONResponse(
        {
            "subscriptions": len(rows),
            "last_success": rows[-1].last_success_at.isoformat()
            if rows and rows[-1].last_success_at
            else None,
        }
    )


@router.post("/api/push/test")
async def push_test(request: Request, session: Session = Depends(get_session)):
    """Eine Testnachricht schicken (prüft die Einrichtung)."""
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    delivered = push.send(
        session,
        "o-energy",
        "Testnachricht - so sieht eine Benachrichtigung aus.",
        url="/",
    )
    return JSONResponse({"ok": True, "delivered": delivered})
