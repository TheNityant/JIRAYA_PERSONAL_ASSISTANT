from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Action, Obligation


OPEN_STATUS = "OPEN"
TERMINAL_STATUSES = {"RESOLVED_BY_USER", "RESOLVED_BY_AGENT", "SUPERSEDED", "CANCELLED"}


class ObligationStore:
    def __init__(self, db: Session):
        self.db = db

    def get(self, obligation_id: str) -> Obligation | None:
        return self.db.get(Obligation, obligation_id)

    def open_for_action(self, action_id: str) -> Obligation | None:
        statement = (
            select(Obligation)
            .where(Obligation.related_action_id == action_id)
            .where(Obligation.status == OPEN_STATUS)
            .order_by(Obligation.updated_at.desc(), Obligation.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(statement).first()

    def upsert_from_reconciliation(
        self,
        *,
        action: Action,
        classification: str,
        reason: str,
        message_id: str,
        message_internal_date_ms: int,
        evidence: dict,
    ) -> Obligation:
        existing = self.open_for_action(action.id)
        now = datetime.now(timezone.utc)
        title = self._title_for(classification)

        if existing:
            existing.obligation_type = classification
            existing.title = title
            existing.description = reason
            existing.trigger_message_id = message_id
            existing.external_thread_id = action.external_thread_id
            existing.context = {
                **(existing.context or {}),
                "latest_classification": classification,
                "trigger_message_id": message_id,
                "trigger_internal_date_ms": message_internal_date_ms,
                "evidence": evidence,
            }
            existing.updated_at = now
            self.db.add(existing)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        obligation = Obligation(
            obligation_type=classification,
            source="gmail",
            source_entity_id=action.source_entity_id,
            related_action_id=action.id,
            external_thread_id=action.external_thread_id,
            trigger_message_id=message_id,
            status=OPEN_STATUS,
            title=title,
            description=reason,
            context={
                "latest_classification": classification,
                "trigger_message_id": message_id,
                "trigger_internal_date_ms": message_internal_date_ms,
                "evidence": evidence,
            },
            resolution={},
            created_at=now,
            updated_at=now,
        )
        self.db.add(obligation)
        self.db.commit()
        self.db.refresh(obligation)
        return obligation

    def note_resolution_candidate(
        self,
        *,
        obligation: Obligation,
        message_id: str,
        resolves: bool,
        confidence: float,
        reason: str,
    ) -> Obligation:
        obligation.context = {
            **(obligation.context or {}),
            "last_resolution_candidate_id": message_id,
            "last_resolution_candidate": {
                "resolves": resolves,
                "confidence": confidence,
                "reason": reason,
            },
        }
        obligation.updated_at = datetime.now(timezone.utc)
        self.db.add(obligation)
        self.db.commit()
        self.db.refresh(obligation)
        return obligation

    def resolve(
        self,
        *,
        obligation: Obligation,
        status: str,
        resolved_at: datetime,
        message_id: str,
        reason: str,
        evidence: dict,
        resolving_action_id: str | None = None,
    ) -> Obligation:
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"Unsupported obligation resolution status: {status}")

        obligation.status = status
        obligation.resolved_at = resolved_at
        obligation.updated_at = datetime.now(timezone.utc)
        obligation.resolution = {
            "status": status,
            "message_id": message_id,
            "reason": reason,
            "evidence": evidence,
            "resolving_action_id": resolving_action_id,
        }
        self.db.add(obligation)
        self.db.commit()
        self.db.refresh(obligation)
        return obligation

    def list_recent(self, *, status: str | None = None, limit: int = 100) -> list[Obligation]:
        statement = select(Obligation)
        if status:
            statement = statement.where(Obligation.status == status)
        statement = statement.order_by(Obligation.updated_at.desc()).limit(limit)
        return list(self.db.scalars(statement).all())

    @staticmethod
    def _title_for(classification: str) -> str:
        return {
            "NEW_OBLIGATION": "Follow-up required",
            "SUPERSEDED_LATER": "Updated information requires review",
            "EXTERNALLY_INVALIDATED": "Previous outcome requires recovery",
        }.get(classification, "Review required")
