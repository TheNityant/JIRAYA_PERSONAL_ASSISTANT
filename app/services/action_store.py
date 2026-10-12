from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Action, Event


class ActionStore:
    def __init__(self, db: Session):
        self.db = db

    def create_email_action(
        self,
        *,
        correlation_id: str,
        source_entity_id: str,
        source_message_id: str,
        sent_message_id: str,
        thread_id: str,
        recipient: str,
        status: str,
        executed_at: datetime,
        verified_at: datetime | None,
        verification_evidence: dict,
    ) -> Action:
        existing = self._by_correlation_id(correlation_id)
        if existing:
            return existing

        action = Action(
            action_type="EMAIL_REPLY",
            source="gmail",
            source_entity_id=source_entity_id,
            correlation_id=correlation_id,
            external_thread_id=thread_id or None,
            initial_status=status,
            current_status=status,
            context={
                "source_message_id": source_message_id,
                "sent_message_id": sent_message_id,
                "recipient": recipient,
            },
            verification_evidence=verification_evidence,
            executed_at=executed_at,
            verified_at=verified_at,
        )
        self.db.add(action)
        self.db.commit()
        self.db.refresh(action)
        return action

    def get(self, action_id: str) -> Action | None:
        return self.db.get(Action, action_id)

    def latest_verified_email_action(self) -> Action | None:
        statement = (
            select(Action)
            .where(Action.action_type == "EMAIL_REPLY")
            .where(Action.initial_status == "VERIFIED_SUCCESS")
            .order_by(Action.verified_at.desc(), Action.created_at.desc())
            .limit(1)
        )
        action = self.db.scalars(statement).first()
        if action:
            return action
        return self.backfill_latest_verified_email_action()

    def backfill_latest_verified_email_action(self) -> Action | None:
        verified = self.db.scalars(
            select(Event)
            .where(Event.event_type == "EMAIL_REPLY_VERIFIED")
            .order_by(Event.occurred_at.desc())
            .limit(1)
        ).first()
        if not verified or not verified.correlation_id or not verified.entity_id:
            return None

        existing = self._by_correlation_id(verified.correlation_id)
        if existing:
            return existing

        sent = self.db.scalars(
            select(Event)
            .where(Event.correlation_id == verified.correlation_id)
            .where(Event.event_type == "EMAIL_REPLY_SENT")
            .order_by(Event.occurred_at.desc())
            .limit(1)
        ).first()
        observed = self.db.scalars(
            select(Event)
            .where(Event.correlation_id == verified.correlation_id)
            .where(Event.event_type == "EMAIL_OBSERVED")
            .order_by(Event.occurred_at.asc())
            .limit(1)
        ).first()
        if not sent or not observed:
            return None

        source_message_id = observed.payload.get("message_id") or verified.entity_id.removeprefix("gmail:")
        sent_message_id = sent.payload.get("sent_message_id") or verified.payload.get("sent_message_id")
        thread_id = sent.payload.get("thread_id") or verified.payload.get("observed_thread_id") or ""
        if not sent_message_id:
            return None

        return self.create_email_action(
            correlation_id=verified.correlation_id,
            source_entity_id=verified.entity_id,
            source_message_id=source_message_id,
            sent_message_id=sent_message_id,
            thread_id=thread_id,
            recipient="",
            status="VERIFIED_SUCCESS",
            executed_at=sent.occurred_at,
            verified_at=verified.occurred_at,
            verification_evidence=verified.payload,
        )

    def apply_reconciliation(
        self,
        *,
        action: Action,
        classification: str,
        reason: str,
        requires_follow_up: bool,
        message_id: str,
        observed_at: datetime,
        evidence: dict,
    ) -> Action:
        if classification in {"SUPERSEDED_LATER", "EXTERNALLY_INVALIDATED"}:
            action.current_status = classification
        elif action.current_status not in {"SUPERSEDED_LATER", "EXTERNALLY_INVALIDATED"}:
            action.current_status = "VERIFIED_SUCCESS"

        action.last_reconciliation = {
            "classification": classification,
            "reason": reason,
            "requires_follow_up": requires_follow_up,
            "message_id": message_id,
            "evidence": evidence,
        }
        action.last_reconciled_at = observed_at
        self.db.add(action)
        self.db.commit()
        self.db.refresh(action)
        return action

    def list_recent(self, limit: int = 50) -> list[Action]:
        statement = select(Action).order_by(Action.created_at.desc()).limit(limit)
        return list(self.db.scalars(statement).all())

    def _by_correlation_id(self, correlation_id: str) -> Action | None:
        return self.db.scalars(
            select(Action).where(Action.correlation_id == correlation_id).limit(1)
        ).first()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
