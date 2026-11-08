"""Cookie-authenticated cloud controls; no worker secrets or arbitrary join URLs."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.meet_cloud.store import ACTIVE, MEET_READ_SCOPE, aware, pack, pause
from app.models import CloudMeetSession, CloudMeetSettings
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(prefix="/meet/cloud", tags=["cloud-meet"])


class Preferences(BaseModel):
    enabled: bool
    consent: bool = False
    prefer_google_transcript: bool = True
    leave_on_speech: bool = True


def availability(db, settings):
    heartbeat = RuntimeStateStore(db).get("cloud-meet-worker") or {}
    try:
        online = datetime.fromisoformat(heartbeat["at"]) > datetime.now(UTC) - timedelta(seconds=90)
    except (KeyError, ValueError, TypeError):
        online = False
    return {
        "configured": settings.meet_cloud_enabled
        and bool(settings.assemblyai_api_key)
        and bool(settings.token_encryption_key)
        and bool(settings.nebius_api_key),
        "worker_online": online,
    }


@router.get("/status")
def status(db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    preference = db.get(CloudMeetSettings, settings.dev_user_id)
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id, provider="google_calendar"
    )
    return {
        **availability(db, settings),
        "enabled": bool(preference and preference.enabled),
        "preferences": preference.preferences if preference else {},
        "google_transcript_authorized": bool(connection and MEET_READ_SCOPE in connection.scopes),
    }


@router.put("/settings")
def configure(body: Preferences, db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    if not body.enabled:
        pause(db, settings.dev_user_id)
        return status(db)
    ready = availability(db, settings)
    if (
        not ready["configured"]
        or not ready["worker_online"]
        or not settings.enable_external_actions
    ):
        raise HTTPException(
            503,
            "Cloud attendance requires an online cloud worker, AssemblyAI, encryption and enabled external actions.",
        )
    if not body.consent:
        raise HTTPException(
            422,
            "Confirm permission to transcribe meetings and process notes before enabling cloud attendance.",
        )
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id, provider="google_calendar"
    )
    if not connection:
        raise HTTPException(409, "Connect Google Calendar first.")
    TokenCipher(settings.token_encryption_key)
    row = db.get(CloudMeetSettings, settings.dev_user_id) or CloudMeetSettings(
        id=settings.dev_user_id, user_id=settings.dev_user_id
    )
    row.enabled = True
    row.updated_at = datetime.now(UTC)
    row.preferences = {
        **body.model_dump(exclude={"enabled", "consent"}),
        "calendar_account": connection.provider_account_id,
        "consented_at": datetime.now(UTC).isoformat(),
    }
    db.add(row)
    db.commit()
    return status(db)


@router.get("/sessions")
def sessions(db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    rows = db.scalars(
        select(CloudMeetSession)
        .where(CloudMeetSession.user_id == settings.dev_user_id)
        .order_by(CloudMeetSession.created_at.desc())
        .limit(30)
    ).all()
    return {"items": [pack(db, settings, row) for row in rows]}


def owned(db, key):
    row = db.get(CloudMeetSession, key)
    if not row or row.user_id != get_settings().dev_user_id:
        raise HTTPException(404, "Meeting session not found.")
    return row


@router.get("/sessions/{key}")
def detail(key: str, db: Annotated[Session, Depends(get_db)]):
    return pack(db, get_settings(), owned(db, key), include_content=True)


@router.post("/sessions/{key}/stop")
def stop(key: str, db: Annotated[Session, Depends(get_db)]):
    row = owned(db, key)
    row.stop_requested = True
    if row.status == "QUEUED":
        row.status = "CANCELLED"
    db.commit()
    return {"stop_requested": True}


@router.post("/sessions/{key}/summary")
def refresh_summary(key: str, db: Annotated[Session, Depends(get_db)]):
    row = owned(db, key)
    if row.status in ("QUEUED",) + ACTIVE or aware(row.ends_at) > datetime.now(UTC):
        raise HTTPException(
            409, "Wait until this meeting's scheduled end before refreshing Google notes."
        )
    if (
        row.summary_state == "RUNNING"
        and row.summary_started_at
        and aware(row.summary_started_at) > datetime.now(UTC) - timedelta(hours=2)
    ):
        raise HTTPException(409, "Summary generation is already running.")
    row.summary_state = "QUEUED"
    row.details = {**row.details, "summary_requested": True}
    db.commit()
    return {"queued": True}


@router.delete("/sessions/{key}")
def delete(key: str, db: Annotated[Session, Depends(get_db)]):
    row = owned(db, key)
    if row.status in ACTIVE:
        raise HTTPException(409, "Stop attendance and wait for it to leave before deleting notes.")
    RuntimeStateStore(db).set(
        "cloud-meet-deleted:" + row.id,
        {"expires_at": (aware(row.ends_at) + timedelta(days=1)).isoformat()},
    )
    db.delete(row)
    db.commit()
    return {"deleted": True}
