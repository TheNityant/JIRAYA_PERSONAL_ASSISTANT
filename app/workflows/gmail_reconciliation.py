from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.integrations.gmail.client import GmailClient
from app.models import Action
from app.providers.base import ModelProvider
from app.reconciler.email_reconciler import EmailReconciler
from app.services.action_store import ActionStore
from app.services.event_ledger import EventLedger


class GmailReconciliationWorkflow:
    def __init__(
        self,
        *,
        db: Session,
        provider: ModelProvider,
        gmail: GmailClient,
    ):
        self.actions = ActionStore(db)
        self.ledger = EventLedger(db)
        self.gmail = gmail
        self.reconciler = EmailReconciler(provider)

    def run(self, *, action_id: str | None = None) -> dict:
        reconciliation_id = str(uuid4())
        action = self.actions.get(action_id) if action_id else self.actions.latest_verified_email_action()
        if action is None:
            return {
                "correlation_id": reconciliation_id,
                "decision": "NO_VERIFIED_ACTION",
            }

        if action.action_type != "EMAIL_REPLY" or action.initial_status != "VERIFIED_SUCCESS":
            return {
                "correlation_id": reconciliation_id,
                "action_id": action.id,
                "decision": "ACTION_NOT_ELIGIBLE",
                "initial_status": action.initial_status,
                "current_status": action.current_status,
            }

        if not action.external_thread_id:
            return self._unobserved(
                reconciliation_id=reconciliation_id,
                action=action,
                reason="verified action has no Gmail thread id",
            )

        try:
            thread_messages = self.gmail.get_thread_messages(action.external_thread_id)
        except Exception as exc:
            return self._unobserved(
                reconciliation_id=reconciliation_id,
                action=action,
                reason=f"Gmail thread could not be observed: {type(exc).__name__}",
            )

        later_messages = self._later_incoming_messages(action, thread_messages)
        if not later_messages:
            return {
                "correlation_id": reconciliation_id,
                "action_id": action.id,
                "decision": "NO_NEW_EVENT",
                "initial_status": action.initial_status,
                "current_status": action.current_status,
            }

        latest = later_messages[-1]
        previous = action.last_reconciliation or {}
        if previous.get("message_id") == latest.id:
            return {
                "correlation_id": reconciliation_id,
                "action_id": action.id,
                "decision": "NO_NEW_EVENT",
                "initial_status": action.initial_status,
                "current_status": action.current_status,
                "last_reconciliation": previous,
            }

        self.ledger.append(
            source="gmail",
            event_type="EMAIL_LATER_EVENT_OBSERVED",
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload={
                "action_id": action.id,
                "original_correlation_id": action.correlation_id,
                "thread_id": action.external_thread_id,
                "message_id": latest.id,
                "from": latest.sender,
                "subject": latest.subject,
                "internal_date_ms": latest.internal_date_ms,
            },
        )

        decision = self.reconciler.reconcile(action=action, later_message=latest)
        observed_at = datetime.fromtimestamp(latest.internal_date_ms / 1000, tz=timezone.utc)
        evidence = {
            "thread_id": latest.thread_id,
            "message_id": latest.id,
            "sender": latest.sender,
            "subject": latest.subject,
            "internal_date_ms": latest.internal_date_ms,
        }
        action = self.actions.apply_reconciliation(
            action=action,
            classification=decision.classification,
            reason=decision.reason,
            requires_follow_up=decision.requires_follow_up,
            message_id=latest.id,
            observed_at=observed_at,
            evidence=evidence,
        )

        event_type = {
            "NEW_OBLIGATION": "ACTION_NEW_OBLIGATION",
            "SUPERSEDED_LATER": "ACTION_SUPERSEDED_LATER",
            "EXTERNALLY_INVALIDATED": "ACTION_EXTERNALLY_INVALIDATED",
            "NO_MATERIAL_CHANGE": "ACTION_STILL_VALID",
        }[decision.classification]
        self.ledger.append(
            source="reconciliator",
            event_type=event_type,
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload={
                "action_id": action.id,
                "original_correlation_id": action.correlation_id,
                **decision.model_dump(),
                "evidence": evidence,
            },
        )

        return {
            "correlation_id": reconciliation_id,
            "action_id": action.id,
            "decision": "RECONCILED",
            "original_action_status": action.initial_status,
            "current_status": action.current_status,
            "classification": decision.classification,
            "original_action_validity": decision.original_action_validity,
            "requires_follow_up": decision.requires_follow_up,
            "reason": decision.reason,
            "later_message": evidence,
        }

    def _unobserved(self, *, reconciliation_id: str, action: Action, reason: str) -> dict:
        self.ledger.append(
            source="reconciliator",
            event_type="ACTION_RECONCILIATION_UNOBSERVED",
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload={
                "action_id": action.id,
                "original_correlation_id": action.correlation_id,
                "reason": reason,
            },
        )
        return {
            "correlation_id": reconciliation_id,
            "action_id": action.id,
            "decision": "UNOBSERVED",
            "original_action_status": action.initial_status,
            "current_status": action.current_status,
            "reason": reason,
        }

    @staticmethod
    def _later_incoming_messages(action: Action, messages: list) -> list:
        reference_time = action.verified_at or action.executed_at
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        threshold_ms = int(reference_time.timestamp() * 1000)
        source_id = action.context.get("source_message_id")
        sent_id = action.context.get("sent_message_id")

        later = [
            message
            for message in messages
            if message.internal_date_ms > threshold_ms
            and message.id not in {source_id, sent_id}
            and "SENT" not in message.labels
        ]
        return sorted(later, key=lambda item: item.internal_date_ms)
