"""Bounded, encrypted desktop relay; no AWS public port or browser bearer URL."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, update

from app.meet_cloud.store import aware
from app.models import AuthSession, CloudMeetBrowserInput, CloudMeetBrowserSetup
from app.security.sessions import COOKIE_NAME, digest
from app.security.tokens import TokenCipher

ACTIVE_SETUP = ("QUEUED", "STARTING", "OPEN", "VERIFYING")
KEYS = {
    "Enter",
    "Tab",
    "Shift+Tab",
    "Backspace",
    "Delete",
    "ArrowLeft",
    "ArrowRight",
    "ArrowUp",
    "ArrowDown",
    "Home",
    "End",
    "Control+A",
    "Escape",
    "Space",
}


class InputEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["click", "key", "text", "scroll"]
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    text: str = Field(default="", max_length=512)
    key: str = Field(default="", max_length=20)
    delta: int = Field(default=0, ge=-800, le=800)

    @model_validator(mode="after")
    def allowed(self):
        if self.kind == "key" and self.key not in KEYS:
            raise ValueError("Unsupported browser key")
        return self


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = Field(ge=1)
    events: list[InputEvent] = Field(min_length=1, max_length=20)


def login_session(db, request: Request, user_id):
    token = request.cookies.get(COOKIE_NAME)
    session = db.get(AuthSession, digest(token)) if token else None
    if not session or session.user_id != user_id or aware(session.expires_at) <= datetime.now(UTC):
        raise HTTPException(401, "Sign in to Jiraya again before opening your meeting browser.")
    return session.token_hash


def finish(db, row, status, reason="", *, commit=True):
    changed = db.execute(
        update(CloudMeetBrowserSetup)
        .where(CloudMeetBrowserSetup.id == row.id, CloudMeetBrowserSetup.status.in_(ACTIVE_SETUP))
        .values(status=status, reason=reason, active_slot=None, frame_ciphertext="")
    ).rowcount
    db.execute(delete(CloudMeetBrowserInput).where(CloudMeetBrowserInput.setup_id == row.id))
    if commit:
        db.commit()
        db.refresh(row)
    return bool(changed)


def expire(db):
    now = datetime.now(UTC)
    for row in db.scalars(
        select(CloudMeetBrowserSetup).where(CloudMeetBrowserSetup.status.in_(ACTIVE_SETUP))
    ).all():
        if aware(row.expires_at) <= now:
            finish(db, row, "EXPIRED", "SETUP_TIMEOUT")
        elif (
            row.status != "QUEUED"
            and row.heartbeat_at
            and aware(row.heartbeat_at) < now - timedelta(seconds=90)
        ):
            finish(db, row, "FAILED", "WORKER_LOST")
    old_ids = list(
        db.scalars(
            select(CloudMeetBrowserSetup.id).where(
                CloudMeetBrowserSetup.expires_at < now - timedelta(days=1)
            )
        ).all()
    )
    if old_ids:
        db.execute(delete(CloudMeetBrowserInput).where(CloudMeetBrowserInput.setup_id.in_(old_ids)))
        db.execute(delete(CloudMeetBrowserSetup).where(CloudMeetBrowserSetup.id.in_(old_ids)))
        db.commit()


def pack_setup(row, settings, include_frame=False):
    result = {
        "id": row.id,
        "status": row.status,
        "reason": row.reason,
        "expires_at": aware(row.expires_at).isoformat(),
        "next_sequence": row.next_sequence,
        "calendar_account": row.calendar_account,
    }
    if include_frame and row.status == "OPEN" and row.frame_ciphertext:
        result["frame"] = json.loads(
            TokenCipher(settings.token_encryption_key).decrypt(row.frame_ciphertext)
        )
    return result


def enqueue_input(db, row, settings, body):
    if row.status != "OPEN":
        raise HTTPException(409, "This browser connection is not open.")
    if body.sequence < row.next_sequence:
        return {"accepted": True, "next_sequence": row.next_sequence}  # Idempotent retry.
    if body.sequence != row.next_sequence:
        raise HTTPException(409, "Browser inputs arrived out of order. Reopen the connection.")
    pending = db.scalars(
        select(CloudMeetBrowserInput.id).where(CloudMeetBrowserInput.setup_id == row.id).limit(20)
    ).all()
    if len(pending) >= 20:
        raise HTTPException(429, "Browser is catching up. Wait a moment before typing again.")
    changed = db.execute(
        update(CloudMeetBrowserSetup)
        .where(
            CloudMeetBrowserSetup.id == row.id,
            CloudMeetBrowserSetup.next_sequence == body.sequence,
            CloudMeetBrowserSetup.status == "OPEN",
        )
        .values(next_sequence=body.sequence + 1)
    ).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, "Browser connection changed. Refresh its status.")
    db.add(
        CloudMeetBrowserInput(
            setup_id=row.id,
            sequence=body.sequence,
            payload_ciphertext=TokenCipher(settings.token_encryption_key).encrypt(
                json.dumps([event.model_dump() for event in body.events])
            ),
        )
    )
    db.commit()
    return {"accepted": True, "next_sequence": body.sequence + 1}


def claim_setup(db, worker_id):
    expire(db)
    row = db.scalar(
        select(CloudMeetBrowserSetup)
        .where(CloudMeetBrowserSetup.status == "QUEUED")
        .order_by(CloudMeetBrowserSetup.created_at)
        .limit(1)
    )
    if not row:
        return None
    changed = db.execute(
        update(CloudMeetBrowserSetup)
        .where(CloudMeetBrowserSetup.id == row.id, CloudMeetBrowserSetup.status == "QUEUED")
        .values(status="STARTING", worker_id=worker_id, heartbeat_at=datetime.now(UTC))
    ).rowcount
    db.commit()
    return row.id if changed else None
