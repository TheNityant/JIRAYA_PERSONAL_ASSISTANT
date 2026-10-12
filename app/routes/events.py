from datetime import timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Event
from app.schemas import EventCreate, EventRead

router = APIRouter(prefix="/events", tags=["event-ledger"])


@router.post("", response_model=EventRead, status_code=201)
def create_event(event_in: EventCreate, db: Session = Depends(get_db)) -> Event:
    values = event_in.model_dump(exclude={"occurred_at"})
    event = Event(**values)
    if event_in.occurred_at is not None:
        occurred = event_in.occurred_at
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=timezone.utc)
        event.occurred_at = occurred
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get("", response_model=list[EventRead])
def list_events(
    limit: int = Query(default=50, ge=1, le=200),
    source: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Event]:
    stmt = select(Event)
    if source:
        stmt = stmt.where(Event.source == source)
    if entity_id:
        stmt = stmt.where(Event.entity_id == entity_id)
    if correlation_id:
        stmt = stmt.where(Event.correlation_id == correlation_id)
    stmt = stmt.order_by(Event.occurred_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())
