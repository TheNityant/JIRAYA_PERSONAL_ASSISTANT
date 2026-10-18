from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Notification
from app.services.notification_store import NotificationStore

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _payload(item: Notification) -> dict:
    return {
        "id": item.id,
        "category": item.category,
        "severity": item.severity,
        "title": item.title,
        "message": item.message,
        "source": item.source,
        "source_entity_id": item.source_entity_id,
        "automation_id": item.automation_id,
        "action_id": item.action_id,
        "obligation_id": item.obligation_id,
        "metadata": item.notification_metadata,
        "read_at": item.read_at,
        "created_at": item.created_at,
    }


@router.get("")
def list_notifications(
    category: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    store = NotificationStore(db)
    items = store.list_recent(
        user_id=settings.dev_user_id,
        category=category,
        unread_only=unread_only,
    )
    return {
        "unread_count": store.unread_count(user_id=settings.dev_user_id),
        "items": [_payload(item) for item in items],
    }


@router.post("/{notification_id}/read")
def mark_notification_read(notification_id: str, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    item = NotificationStore(db).mark_read(
        notification_id=notification_id,
        user_id=settings.dev_user_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _payload(item)
