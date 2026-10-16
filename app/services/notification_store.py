from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification


class NotificationStore:
    def __init__(self, db: Session):
        self.db = db

    def create_once(
        self,
        *,
        user_id: str,
        category: str,
        title: str,
        message: str,
        severity: str = "INFO",
        source: str = "jiraya",
        source_entity_id: str | None = None,
        automation_id: str | None = None,
        action_id: str | None = None,
        obligation_id: str | None = None,
        dedupe_key: str | None = None,
        metadata: dict | None = None,
    ) -> Notification:
        if dedupe_key:
            existing = self.db.scalars(
                select(Notification).where(Notification.dedupe_key == dedupe_key).limit(1)
            ).first()
            if existing:
                return existing

        notification = Notification(
            user_id=user_id,
            category=category,
            severity=severity,
            title=title,
            message=message,
            source=source,
            source_entity_id=source_entity_id,
            automation_id=automation_id,
            action_id=action_id,
            obligation_id=obligation_id,
            dedupe_key=dedupe_key,
            notification_metadata=metadata or {},
        )
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        return notification

    def list_recent(
        self,
        *,
        user_id: str,
        category: str | None = None,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[Notification]:
        statement = select(Notification).where(Notification.user_id == user_id)
        if category:
            statement = statement.where(Notification.category == category)
        if unread_only:
            statement = statement.where(Notification.read_at.is_(None))
        statement = statement.order_by(Notification.created_at.desc()).limit(limit)
        return list(self.db.scalars(statement).all())

    def mark_read(self, *, notification_id: str, user_id: str) -> Notification | None:
        notification = self.db.get(Notification, notification_id)
        if notification is None or notification.user_id != user_id:
            return None
        if notification.read_at is None:
            notification.read_at = datetime.now(timezone.utc)
            self.db.add(notification)
            self.db.commit()
            self.db.refresh(notification)
        return notification

    def unread_count(self, *, user_id: str) -> int:
        return len(self.list_recent(user_id=user_id, unread_only=True, limit=1000))
