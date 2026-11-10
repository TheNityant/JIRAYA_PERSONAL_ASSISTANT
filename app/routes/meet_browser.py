"""Authenticated, same-session remote browser setup through the existing API proxy."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.meet_cloud.identity import browser_status, identity_mode, set_browser_status
from app.meet_cloud.portal_store import (
    ACTIVE_SETUP,
    BrowserInput,
    enqueue_input,
    expire,
    finish,
    login_session,
    pack_setup,
)
from app.meet_cloud.store import ACTIVE
from app.models import CloudMeetBrowserSetup, CloudMeetSession, CloudMeetSettings
from app.routes.meet_cloud import availability
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(prefix="/meet/cloud/browser", tags=["cloud-meet-browser"])
DB = Annotated[Session, Depends(get_db)]


class StartBrowser(BaseModel):
    consent: bool = False


def private_response(response):
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"


def trusted_write(request, settings):
    allowed = {origin.strip().rstrip("/") for origin in settings.frontend_origins.split(",")}
    if request.headers.get("origin", "").rstrip("/") not in allowed:
        raise HTTPException(403, "Open the meeting browser from the Jiraya frontend.")


def owned_setup(db, key, request):
    settings = get_settings()
    session_hash = login_session(db, request, settings.dev_user_id)
    expire(db)
    row = db.get(CloudMeetBrowserSetup, key)
    if not row or row.user_id != settings.dev_user_id or row.auth_session_hash != session_hash:
        raise HTTPException(404, "Browser connection not found in this sign-in session.")
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id, provider="google_calendar"
    )
    if row.status in ACTIVE_SETUP and (
        not connection or connection.provider_account_id != row.calendar_account
    ):
        finish(db, row, "CANCELLED", "CALENDAR_CHANGED")
    return row


@router.post("/sessions")
def start(body: StartBrowser, request: Request, response: Response, db: DB):
    private_response(response)
    settings = get_settings()
    trusted_write(request, settings)
    session_hash = login_session(db, request, settings.dev_user_id)
    if not body.consent:
        raise HTTPException(
            422, "Confirm that you trust this remote Google browser before connecting."
        )
    try:
        TokenCipher(settings.token_encryption_key)
    except RuntimeError:
        raise HTTPException(
            503, "The browser connection relay requires valid server encryption settings."
        ) from None
    heartbeat = RuntimeStateStore(db).get("cloud-meet-worker") or {}
    if (
        not availability(db, settings)["worker_online"]
        or not heartbeat.get("browser_portal")
        or identity_mode(db, settings) != "google_profile"
    ):
        raise HTTPException(
            503,
            "The meeting server must enable and run the Google browser connection portal first.",
        )
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id, provider="google_calendar"
    )
    if not connection:
        raise HTTPException(409, "Connect Google Calendar first.")
    preference = db.get(CloudMeetSettings, settings.dev_user_id)
    active = db.scalar(
        select(CloudMeetSession.id)
        .where(
            CloudMeetSession.user_id == settings.dev_user_id, CloudMeetSession.status.in_(ACTIVE)
        )
        .limit(1)
    )
    if (preference and preference.enabled) or active:
        raise HTTPException(
            409, "Pause attendance and wait for your active meeting to leave before browser setup."
        )
    expire(db)
    existing = db.scalar(
        select(CloudMeetBrowserSetup)
        .where(
            CloudMeetBrowserSetup.user_id == settings.dev_user_id,
            CloudMeetBrowserSetup.status.in_(ACTIVE_SETUP),
        )
        .limit(1)
    )
    if existing:
        if existing.auth_session_hash != session_hash:
            raise HTTPException(
                409,
                "Close the browser connection on your other signed-in device, or wait for it to expire.",
            )
        return pack_setup(existing, settings)
    recent = db.scalars(
        select(CloudMeetBrowserSetup.id)
        .where(
            CloudMeetBrowserSetup.user_id == settings.dev_user_id,
            CloudMeetBrowserSetup.created_at > datetime.now(UTC) - timedelta(minutes=10),
        )
        .limit(5)
    ).all()
    if len(recent) >= 5:
        raise HTTPException(429, "Too many browser setup attempts. Try again in ten minutes.")
    now = datetime.now(UTC)
    row = CloudMeetBrowserSetup(
        user_id=settings.dev_user_id,
        calendar_account=connection.provider_account_id,
        auth_session_hash=session_hash,
        active_slot=1,
        expires_at=now + timedelta(minutes=10),
        last_viewed_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            409, "The meeting browser setup service is busy. Try again shortly."
        ) from None
    previous = browser_status(db, settings.dev_user_id, connection.provider_account_id)
    set_browser_status(
        db,
        settings.dev_user_id,
        connection.provider_account_id,
        "RECONNECT_REQUIRED" if previous["last_verified_at"] else "SETUP_REQUIRED",
        notify=False,
    )
    return pack_setup(row, settings)


@router.get("/sessions/{key}")
def view(key: str, request: Request, response: Response, db: DB):
    private_response(response)
    row = owned_setup(db, key, request)
    row.last_viewed_at = datetime.now(UTC)
    db.commit()
    return pack_setup(row, get_settings(), include_frame=True)


@router.post("/sessions/{key}/input")
def browser_input(key: str, body: BrowserInput, request: Request, response: Response, db: DB):
    private_response(response)
    trusted_write(request, get_settings())
    row = owned_setup(db, key, request)
    return enqueue_input(db, row, get_settings(), body)


@router.post("/sessions/{key}/cancel")
def cancel(key: str, request: Request, response: Response, db: DB):
    private_response(response)
    trusted_write(request, get_settings())
    row = owned_setup(db, key, request)
    if row.status in ACTIVE_SETUP:
        finish(db, row, "CANCELLED", "CLOSED_BY_USER")
    return pack_setup(row, get_settings())
