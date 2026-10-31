"""Account-authenticated device registration and narrowly scoped Calendar feeds."""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.integrations.calendar.connection import get_calendar_client
from app.meet_agent.link_detector import extract_meet_links, is_valid_meet_link
from app.security.sessions import digest
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

router = APIRouter(tags=["meet"])


@router.post("/meet/devices")
def register_device(response: Response, db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    token = secrets.token_urlsafe(40)
    RuntimeStateStore(db).set(
        "meet-device:" + digest(token),
        {
            "user_id": settings.dev_user_id,
            "email": settings.dev_user_email,
            "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return {"token": token, "expires_in_days": 30}


@router.get("/meet/device/events")
def upcoming_meetings(db: Annotated[Session, Depends(get_db)]):
    settings = get_settings()
    if not settings.enable_external_actions:
        return {
            "items": [],
            "status": "EXTERNAL_ACTIONS_DISABLED",
            "account_id": settings.dev_user_id,
        }
    connection = ConnectionStore(db).get_active(
        user_id=settings.dev_user_id, provider="google_calendar"
    )
    calendar_account = connection.provider_account_id if connection else settings.dev_user_id
    now = datetime.now(UTC)
    try:
        events = get_calendar_client(db, settings).list_events(
            calendar_id="primary",
            time_min=(now - timedelta(minutes=2)).isoformat(),
            time_max=(now + timedelta(days=1)).isoformat(),
            max_results=100,
        )
    except Exception as exc:
        raise HTTPException(503, "Reconnect Calendar or check its access.") from exc
    items = []
    for event in events:
        if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
            continue
        if any(
            row.get("self") and row.get("responseStatus") == "declined"
            for row in event.get("attendees", [])
        ):
            continue
        start, end = event.get("start", {}).get("dateTime"), event.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        links = extract_meet_links(
            str(event.get("location") or "") + "\n" + str(event.get("description") or "")
        )
        conference = event.get("hangoutLink")
        if conference and is_valid_meet_link(conference) and conference not in links:
            links.append(conference)
        for point in (event.get("conferenceData") or {}).get("entryPoints", []):
            url = point.get("uri", "")
            if is_valid_meet_link(url) and url not in links:
                links.append(url)
        if len(links) == 1:
            items.append(
                {
                    "id": event["id"],
                    "url": links[0],
                    "start": start,
                    "end": end,
                    "summary": event.get("summary") or "Meeting",
                }
            )
    return {
        "items": items,
        "status": "READY",
        "account_id": settings.dev_user_id,
        "calendar_account": calendar_account,
    }


@router.delete("/meet/device/authorization")
def revoke_device(request: Request, db: Annotated[Session, Depends(get_db)]):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    store = RuntimeStateStore(db)
    key = "meet-device:" + digest(token)
    record = store.get(key)
    if record and record.get("user_id") == get_settings().dev_user_id:
        store.delete(key)
    return {"revoked": True}
