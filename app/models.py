import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    action_type: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_entity_id: Mapped[str] = mapped_column(String(255), index=True)
    correlation_id: Mapped[str] = mapped_column(String(255), index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    initial_status: Mapped[str] = mapped_column(String(80), index=True)
    current_status: Mapped[str] = mapped_column(String(80), index=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_reconciliation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Obligation(Base):
    """A first-class piece of work that remains outstanding after an observed event.

    Events are facts, Actions are things Jiraya/user did, and Obligations represent
    work that still needs to be completed. Keeping them separate lets Jiraya close a
    stale alert when the user handles something manually outside the agent.
    """

    __tablename__ = "obligations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    obligation_type: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_entity_id: Mapped[str] = mapped_column(String(255), index=True)
    related_action_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(80), index=True, default="OPEN")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(2000), default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
