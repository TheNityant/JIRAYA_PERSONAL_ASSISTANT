from typing import Any

from sqlalchemy.orm import Session

from app.models import Event


class EventLedger:
    """Small append-oriented facade used by workflows.

    Each append is committed immediately so evidence recorded before a later failure
    is not lost with the rest of the workflow.
    """

    def __init__(self, db: Session):
        self.db = db

    def append(
        self,
        *,
        source: str,
        event_type: str,
        entity_id: str | None,
        correlation_id: str,
        payload: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            source=source,
            event_type=event_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event
