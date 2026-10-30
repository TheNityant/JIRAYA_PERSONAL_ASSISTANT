import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.calendar.client import CalendarClient
from app.integrations.calendar.connection import get_calendar_client
from app.integrations.calendar.oauth import GOOGLE_CALENDAR_SCOPES, build_calendar_oauth_flow
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore
from app.runtime.status import monitor_status

router = APIRouter(tags=["calendar"])


def _frontend_redirect(*, status: str, detail: str | None = None) -> RedirectResponse:
    settings = get_settings()
    query = {"connection": "google_calendar", "status": status}
    if detail:
        query["detail"] = detail[:240]
    return RedirectResponse(f"{settings.frontend_base_url}/?{urlencode(query)}", status_code=302)


def _pkce_key(state: str) -> str:
    return f"oauth-calendar-pkce:{state}"


def _health_key(user_id: str) -> str:
    return f"oauth-calendar-health:{user_id}"


def _record_health(db: Session, *, user_id: str, status: str, detail: str | None = None) -> None:
    RuntimeStateStore(db).set(_health_key(user_id), {"status": status, "detail": detail})


@router.get("/connections/google/calendar/start")
def start_calendar_connection(response_mode: str | None = Query(default=None), db: Session = Depends(get_db)):
    settings = get_settings()
    store = ConnectionStore(db)
    store.ensure_user(user_id=settings.dev_user_id, email=settings.dev_user_email)
    try:
        TokenCipher(settings.token_encryption_key)
        state_record = store.create_oauth_state(
            user_id=settings.dev_user_id,
            provider="google_calendar",
        )
        flow = build_calendar_oauth_flow(
            settings,
            state=state_record.state,
            autogenerate_code_verifier=True,
        )
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        verifier = str(flow.code_verifier or "")
        if not verifier:
            raise RuntimeError("Google Calendar OAuth PKCE verifier was not generated.")
        RuntimeStateStore(db).set(_pkce_key(state_record.state), {"code_verifier": verifier})
        _record_health(db, user_id=settings.dev_user_id, status="AUTHORIZING")
        if response_mode == "json":
            return {"authorization_url": authorization_url}
        return RedirectResponse(authorization_url, status_code=302)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:220]}"
        _record_health(db, user_id=settings.dev_user_id, status="ERROR", detail=detail)
        raise HTTPException(status_code=503, detail="Google Calendar connection is unavailable. Please contact the app owner." if response_mode == "json" else detail) from exc


@router.get("/connections/google/calendar/callback")
def calendar_connection_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    store = ConnectionStore(db)
    runtime = RuntimeStateStore(db)
    verifier_state = runtime.get(_pkce_key(state)) or {}
    verifier = str(verifier_state.get("code_verifier") or "")
    state_record = store.consume_oauth_state(state=state, provider="google_calendar")

    if state_record is None:
        return _frontend_redirect(status="error", detail="invalid_or_expired_state")
    if error:
        runtime.delete(_pkce_key(state))
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail=error)
        return _frontend_redirect(status="error", detail=error)
    if not code or not verifier:
        detail = "missing_code" if not code else "pkce_verifier_missing_or_expired"
        runtime.delete(_pkce_key(state))
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail=detail)
        return _frontend_redirect(status="error", detail=detail)

    try:
        if settings.app_env.lower() == "development":
            os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
        flow = build_calendar_oauth_flow(
            settings,
            state=state,
            code_verifier=verifier,
            autogenerate_code_verifier=False,
        )
        flow.fetch_token(code=code)
        credentials = flow.credentials
        calendar = CalendarClient(credentials)
        primary = calendar.get_primary_calendar()
        account = str(primary.get("id") or primary.get("summary") or "google-calendar")
        cipher = TokenCipher(settings.token_encryption_key)
        store.upsert(
            user_id=state_record.user_id,
            provider="google_calendar",
            provider_account_id=account,
            display_name=str(primary.get("summaryOverride") or primary.get("summary") or account),
            scopes=list(credentials.scopes or GOOGLE_CALENDAR_SCOPES),
            credential_ciphertext=cipher.encrypt(credentials.to_json()),
            token_expires_at=credentials.expiry,
            metadata={
                "primary_calendar_id": primary.get("id"),
                "time_zone": primary.get("timeZone"),
                "access_role": primary.get("accessRole"),
            },
        )
        _record_health(db, user_id=state_record.user_id, status="CONNECTED")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc)[:220]}"
        _record_health(db, user_id=state_record.user_id, status="ERROR", detail=detail)
        return _frontend_redirect(status="error", detail=detail)
    finally:
        runtime.delete(_pkce_key(state))

    return _frontend_redirect(status="connected")


@router.post("/connections/google/calendar/disconnect")
def disconnect_calendar(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    connection = ConnectionStore(db).disconnect(
        user_id=settings.dev_user_id,
        provider="google_calendar",
    )
    _record_health(db, user_id=settings.dev_user_id, status="DISCONNECTED")
    return {"disconnected": connection is not None, "provider": "google_calendar"}


class FreeBusyRequest(BaseModel):
    time_min: str
    time_max: str
    calendar_ids: list[str] = Field(default_factory=lambda: ["primary"])
    time_zone: str | None = None


class CalendarEventRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=500)
    start: str
    end: str
    time_zone: str | None = None
    description: str = ""
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    calendar_id: str = "primary"
    send_updates: str = "none"


class CalendarEventPatch(BaseModel):
    summary: str | None = None
    start: str | None = None
    end: str | None = None
    time_zone: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None
    calendar_id: str = "primary"
    send_updates: str = "none"


def _event_payload(request: CalendarEventRequest | CalendarEventPatch, *, partial: bool) -> dict:
    raw = request.model_dump(exclude_none=partial)
    raw.pop("calendar_id", None)
    raw.pop("send_updates", None)
    time_zone = raw.pop("time_zone", None)
    if "start" in raw:
        raw["start"] = {"dateTime": raw["start"], **({"timeZone": time_zone} if time_zone else {})}
    if "end" in raw:
        raw["end"] = {"dateTime": raw["end"], **({"timeZone": time_zone} if time_zone else {})}
    if "attendees" in raw:
        raw["attendees"] = [{"email": email} for email in raw["attendees"]]
    return raw


@router.get("/calendar/status")
def calendar_status(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id,
        provider="google_calendar",
    )
    health = RuntimeStateStore(db).get(_health_key(settings.dev_user_id)) or {}
    connected = bool(connection and health.get("status") != "RECONNECT_REQUIRED" and all(scope in (connection.scopes or []) for scope in GOOGLE_CALENDAR_SCOPES))
    return {
        "connected": connected,
        "account": connection.provider_account_id if connected else None,
        "scopes": list(connection.scopes or []) if connection else [],
        "oauth_configured": bool(settings.google_oauth_client_id and settings.google_oauth_client_secret),
        "health": health,
        "monitor": monitor_status(
            RuntimeStateStore(db).get(f"calendar-monitor-health:{settings.dev_user_id}") or {},
            enabled=settings.calendar_background_monitor_enabled,
            poll_seconds=settings.calendar_background_poll_seconds,
        ),
        "background_monitor_configured": settings.calendar_background_monitor_enabled,
        "poll_seconds": settings.calendar_background_poll_seconds,
    }


@router.get("/calendar/events")
def list_calendar_events(
    calendar_id: str = Query(default="primary"),
    time_min: str | None = Query(default=None),
    time_max: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    try:
        start_dt = datetime.fromisoformat(time_min) if time_min else datetime.now(UTC)
        end_dt = datetime.fromisoformat(time_max) if time_max else start_dt + timedelta(days=30)
        if start_dt.tzinfo is None or end_dt.tzinfo is None or end_dt <= start_dt:
            raise ValueError("Use timezone-aware dates with an end after the start")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Provide a valid date window with timezone offsets and an end after the start") from exc
    start, end = start_dt.isoformat(), end_dt.isoformat()
    try:
        items = get_calendar_client(db, settings).list_events(
            calendar_id=calendar_id,
            time_min=start,
            time_max=end,
            max_results=limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Calendar unavailable: {type(exc).__name__}: {exc}") from exc
    return {"calendar_id": calendar_id, "items": items, "time_min": start, "time_max": end, "limit": limit,
            "limit_reached": len(items) >= limit}


@router.post("/calendar/freebusy")
def calendar_freebusy(request: FreeBusyRequest, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    try:
        return get_calendar_client(db, settings).freebusy(
            time_min=request.time_min,
            time_max=request.time_max,
            calendar_ids=request.calendar_ids,
            time_zone=request.time_zone,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Calendar unavailable: {type(exc).__name__}: {exc}") from exc


@router.post("/calendar/events", status_code=201)
def create_calendar_event(request: CalendarEventRequest, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.enable_external_actions:
        raise HTTPException(status_code=403, detail="ENABLE_EXTERNAL_ACTIONS is false")
    try:
        return get_calendar_client(db, settings).create_event(
            calendar_id=request.calendar_id,
            event=_event_payload(request, partial=False),
            send_updates=request.send_updates,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Calendar action failed: {type(exc).__name__}: {exc}") from exc


@router.patch("/calendar/events/{event_id}")
def patch_calendar_event(
    event_id: str,
    request: CalendarEventPatch,
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if not settings.enable_external_actions:
        raise HTTPException(status_code=403, detail="ENABLE_EXTERNAL_ACTIONS is false")
    try:
        return get_calendar_client(db, settings).patch_event(
            calendar_id=request.calendar_id,
            event_id=event_id,
            changes=_event_payload(request, partial=True),
            send_updates=request.send_updates,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Calendar action failed: {type(exc).__name__}: {exc}") from exc


@router.delete("/calendar/events/{event_id}", status_code=204)
def delete_calendar_event(
    event_id: str,
    calendar_id: str = Query(default="primary"),
    send_updates: str = Query(default="none"),
    db: Session = Depends(get_db),
) -> None:
    settings = get_settings()
    if not settings.enable_external_actions:
        raise HTTPException(status_code=403, detail="ENABLE_EXTERNAL_ACTIONS is false")
    try:
        get_calendar_client(db, settings).delete_event(
            calendar_id=calendar_id,
            event_id=event_id,
            send_updates=send_updates,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Calendar action failed: {type(exc).__name__}: {exc}") from exc


@router.post("/runtime/calendar/poll")
def poll_calendar(db: Session = Depends(get_db)) -> dict:
    from app.runtime.calendar_runtime import CalendarRuntimeService
    try:
        return CalendarRuntimeService(db=db, settings=get_settings()).poll()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Calendar sync failed. Check Calendar connection and runtime status.") from exc
