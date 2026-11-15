"""Routen für die Passkey-Anmeldung (WebAuthn)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from oenergy.config import get_settings
from oenergy.db.base import get_session
from oenergy.services.webauthn_service import (
    authentication_options,
    context_for,
    delete_credential,
    finish_authentication,
    finish_registration,
    list_credentials,
    registration_options,
)
from oenergy.web.auth import current_user

router = APIRouter()


def _context(request: Request):
    settings = get_settings()
    origin = str(request.base_url).rstrip("/")
    return context_for(origin, settings), settings


def _not_ready(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


@router.post("/api/auth/passkey/login/options")
async def passkey_login_options(request: Request, session: Session = Depends(get_session)):
    context, settings = _context(request)
    if not settings.webauthn_enabled:
        return _not_ready("Passkeys sind deaktiviert")
    if not list_credentials(session):
        return _not_ready("Es ist noch kein Passkey registriert")

    options, challenge = authentication_options(session, context)
    request.session["webauthn_challenge"] = challenge.hex()
    return JSONResponse({"options": options, "next": request.query_params.get("next", "/")})


@router.post("/api/auth/passkey/login/verify")
async def passkey_login_verify(request: Request, session: Session = Depends(get_session)):
    context, settings = _context(request)
    payload = await request.json()
    challenge = _challenge_from_session(request)
    if not challenge:
        return _not_ready("Keine Anmeldeaufforderung gefunden - bitte erneut versuchen")

    try:
        credential = finish_authentication(session, context, payload.get("credential") or payload, challenge)
    except Exception as exc:
        return JSONResponse({"error": f"Anmeldung fehlgeschlagen: {exc}"}, status_code=401)

    request.session["user"] = settings.auth_username
    request.session.pop("webauthn_challenge", None)
    return JSONResponse({"ok": True, "label": credential.label})


@router.post("/api/auth/passkey/register/options")
async def passkey_register_options(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    context, settings = _context(request)
    if not settings.webauthn_enabled:
        return _not_ready("Passkeys sind deaktiviert")

    options, challenge = registration_options(session, context, settings)
    request.session["webauthn_challenge"] = challenge.hex()
    return JSONResponse({"options": options})


@router.post("/api/auth/passkey/register/verify")
async def passkey_register_verify(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    context, settings = _context(request)
    payload = await request.json()
    challenge = _challenge_from_session(request)
    if not challenge:
        return _not_ready("Keine Registrierungsaufforderung gefunden - bitte erneut versuchen")

    try:
        credential = finish_registration(
            session,
            context,
            payload.get("credential") or payload,
            challenge,
            label=payload.get("label"),
        )
    except Exception as exc:
        return JSONResponse({"error": f"Registrierung fehlgeschlagen: {exc}"}, status_code=400)

    request.session.pop("webauthn_challenge", None)
    return JSONResponse({"ok": True, "id": credential.credential_id, "label": credential.label})


@router.get("/api/auth/passkeys")
async def passkey_list(request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    credentials = list_credentials(session)
    return JSONResponse(
        {
            "passkeys": [
                {
                    "id": cred.credential_id,
                    "label": cred.label,
                    "created_at": cred.created_at.isoformat() if cred.created_at else None,
                    "last_used_at": cred.last_used_at.isoformat() if cred.last_used_at else None,
                }
                for cred in credentials
            ]
        }
    )


@router.delete("/api/auth/passkeys/{credential_id}")
async def passkey_delete(credential_id: str, request: Request, session: Session = Depends(get_session)):
    if not current_user(request):
        return JSONResponse({"error": "nicht angemeldet"}, status_code=401)
    removed = delete_credential(session, credential_id)
    return JSONResponse({"ok": removed}, status_code=200 if removed else 404)


# --- Hilfsfunktionen -------------------------------------------------------


def _challenge_from_session(request: Request) -> bytes | None:
    """Die Challenge aus der Sitzung lesen (sie wird dort als Hex abgelegt)."""
    value = request.session.get("webauthn_challenge")
    if not value:
        return None
    try:
        return bytes.fromhex(value)
    except ValueError:
        return None
