"""Durable queue: claim once; crashed joins are never silently replayed."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models import CloudMeetSession, CloudMeetSettings
from app.security.tokens import TokenCipher
from app.services.runtime_state_store import RuntimeStateStore

MEET_READ_SCOPE = "https://www.googleapis.com/auth/meetings.space.readonly"
ACTIVE = ("STARTING", "JOINED")


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def enqueue(db, user_id, account, event):
    start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
    key = hashlib.sha256((user_id + account + event["id"] + start.isoformat()).encode()).hexdigest()
    tombstone = RuntimeStateStore(db).get("cloud-meet-deleted:" + key)
    if tombstone and datetime.fromisoformat(tombstone["expires_at"]) > datetime.now(UTC):
        return
    if db.get(CloudMeetSession, key):
        return
    db.add(
        CloudMeetSession(
            id=key,
            user_id=user_id,
            calendar_account=account,
            event_id=event["id"],
            meeting_url=event["url"],
            title=event["summary"][:500],
            starts_at=start,
            ends_at=end,
            status="QUEUED",
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # Another worker discovered the same event.


def claim(db, worker_id, now=None):
    now = now or datetime.now(UTC)
    # A crashed process might have reached Meet. Never automatically launch it again.
    db.execute(
        update(CloudMeetSession)
        .where(
            CloudMeetSession.status.in_(ACTIVE),
            CloudMeetSession.heartbeat_at < now - timedelta(seconds=90),
        )
        .values(
            status="REVIEW_REQUIRED",
            details={"reason": "Worker heartbeat lost. Attendance was not replayed."},
        )
    )
    db.execute(
        update(CloudMeetSession)
        .where(
            CloudMeetSession.status == "QUEUED",
            CloudMeetSession.starts_at < now - timedelta(minutes=2),
        )
        .values(status="MISSED")
    )
    db.commit()
    ids = db.scalars(
        select(CloudMeetSession.id)
        .where(
            CloudMeetSession.status == "QUEUED",
            CloudMeetSession.stop_requested.is_(False),
            CloudMeetSession.starts_at <= now + timedelta(seconds=30),
            CloudMeetSession.starts_at >= now - timedelta(minutes=2),
            CloudMeetSession.ends_at > now,
        )
        .order_by(CloudMeetSession.starts_at)
        .limit(20)
    ).all()
    for key in ids:
        changed = db.execute(
            update(CloudMeetSession)
            .where(
                CloudMeetSession.id == key,
                CloudMeetSession.status == "QUEUED",
                CloudMeetSession.stop_requested.is_(False),
            )
            .values(status="STARTING", worker_id=worker_id, heartbeat_at=now)
        ).rowcount
        db.commit()
        if changed:
            return key
    return None


def pack(db, settings, row, *, include_content=False):
    result = {
        "id": row.id,
        "title": row.title,
        "start": aware(row.starts_at).isoformat(),
        "end": aware(row.ends_at).isoformat(),
        "status": row.status,
        "stop_requested": row.stop_requested,
        "details": {
            key: value for key, value in row.details.items() if key != "prejoin_debug_ciphertext"
        },
        "has_prejoin_debug": bool(row.details.get("prejoin_debug_ciphertext")),
        "has_transcript": bool(row.transcript_ciphertext),
    }
    if include_content:
        cipher = TokenCipher(settings.token_encryption_key)
        if row.details.get("prejoin_debug_ciphertext"):
            result["prejoin_debug"] = json.loads(
                cipher.decrypt(row.details["prejoin_debug_ciphertext"])
            )
        result["summary"] = cipher.decrypt(row.summary_ciphertext) if row.summary_ciphertext else ""
        result["transcript"] = (
            json.loads(cipher.decrypt(row.transcript_ciphertext))
            if row.transcript_ciphertext
            else []
        )
    return result


def pause(db, user_id):
    preference = db.get(CloudMeetSettings, user_id)
    if preference:
        preference.enabled = False
    db.execute(
        update(CloudMeetSession)
        .where(
            CloudMeetSession.user_id == user_id, CloudMeetSession.status.in_(("QUEUED",) + ACTIVE)
        )
        .values(stop_requested=True)
    )
    db.execute(
        update(CloudMeetSession)
        .where(CloudMeetSession.user_id == user_id, CloudMeetSession.status == "QUEUED")
        .values(status="CANCELLED")
    )
    db.commit()


def claim_summary(db, key):
    now = datetime.now(UTC)
    changed = db.execute(
        update(CloudMeetSession)
        .where(CloudMeetSession.id == key, CloudMeetSession.summary_state == "QUEUED")
        .values(summary_state="RUNNING", summary_started_at=now)
    ).rowcount
    db.commit()
    return bool(changed)
