from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.integrations.gmail.client import GmailClient
from app.models import Action, Obligation
from app.providers.base import ModelProvider
from app.reconciler.email_reconciler import EmailReconciler
from app.reconciler.obligation_resolver import ObligationResolver
from app.services.action_store import ActionStore
from app.services.event_ledger import EventLedger
from app.services.obligation_store import ObligationStore


class GmailReconciliationWorkflow:
    def __init__(
        self,
        *,
        db: Session,
        provider: ModelProvider,
        gmail: GmailClient,
    ):
        self.actions = ActionStore(db)
        self.obligations = ObligationStore(db)
        self.ledger = EventLedger(db)
        self.gmail = gmail
        self.reconciler = EmailReconciler(provider)
        self.obligation_resolver = ObligationResolver(provider)

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

        # A Phase-2A reconciliation may already have returned requires_follow_up=True
        # before the obligations table existed. Materialize that state once so an
        # existing live demo can continue into Phase 2B without repeating the email.
        open_obligation = self.obligations.open_for_action(action.id)
        if open_obligation is None:
            open_obligation = self._backfill_obligation_if_needed(
                reconciliation_id=reconciliation_id,
                action=action,
            )

        # A later SENT message is only a resolution candidate. Jiraya must still decide
        # whether its contents actually satisfy the open obligation; a simple "thanks"
        # should not clear an interview-availability request.
        if open_obligation:
            resolution_message = self._latest_sent_after_obligation(open_obligation, thread_messages)
            if resolution_message:
                return self._evaluate_sent_resolution_candidate(
                    reconciliation_id=reconciliation_id,
                    action=action,
                    obligation=open_obligation,
                    message=resolution_message,
                )

        later_messages = self._later_incoming_messages(action, thread_messages)
        if not later_messages:
            return {
                "correlation_id": reconciliation_id,
                "action_id": action.id,
                "decision": "NO_NEW_EVENT",
                "initial_status": action.initial_status,
                "current_status": action.current_status,
                "obligation": self._obligation_payload(open_obligation),
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
                "obligation": self._obligation_payload(open_obligation),
            }

        observed_payload = {
            "action_id": action.id,
            "original_correlation_id": action.correlation_id,
            "thread_id": action.external_thread_id,
            "message_id": latest.id,
            "from": latest.sender,
            "subject": latest.subject,
            "internal_date_ms": latest.internal_date_ms,
            "body_preview": latest.body[:2000],
        }
        self.ledger.append(
            source="gmail",
            event_type="EMAIL_LATER_EVENT_OBSERVED",
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload=observed_payload,
        )

        decision = self.reconciler.reconcile(action=action, later_message=latest)
        observed_at = datetime.fromtimestamp(latest.internal_date_ms / 1000, tz=timezone.utc)
        evidence = {
            "thread_id": latest.thread_id,
            "message_id": latest.id,
            "sender": latest.sender,
            "subject": latest.subject,
            "body_preview": latest.body[:2000],
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

        obligation = open_obligation
        if decision.requires_follow_up:
            existed = open_obligation is not None
            obligation = self.obligations.upsert_from_reconciliation(
                action=action,
                classification=decision.classification,
                reason=decision.reason,
                message_id=latest.id,
                message_internal_date_ms=latest.internal_date_ms,
                evidence=evidence,
            )
            self.ledger.append(
                source="reconciliator",
                event_type="OBLIGATION_UPDATED" if existed else "OBLIGATION_OPENED",
                entity_id=action.source_entity_id,
                correlation_id=reconciliation_id,
                payload={
                    "obligation_id": obligation.id,
                    "action_id": action.id,
                    "status": obligation.status,
                    "classification": decision.classification,
                    "trigger_message_id": latest.id,
                    "description": decision.reason,
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
            "obligation": self._obligation_payload(obligation),
        }

    def _backfill_obligation_if_needed(
        self,
        *,
        reconciliation_id: str,
        action: Action,
    ) -> Obligation | None:
        previous = action.last_reconciliation or {}
        if not previous.get("requires_follow_up") or not previous.get("message_id"):
            return None
        evidence = previous.get("evidence") or {}
        internal_date_ms = int(evidence.get("internal_date_ms") or 0)
        if internal_date_ms <= 0:
            return None

        obligation = self.obligations.upsert_from_reconciliation(
            action=action,
            classification=previous.get("classification") or "NEW_OBLIGATION",
            reason=previous.get("reason") or "A prior reconciliation requires follow-up.",
            message_id=previous["message_id"],
            message_internal_date_ms=internal_date_ms,
            evidence=evidence,
        )
        self.ledger.append(
            source="reconciliator",
            event_type="OBLIGATION_BACKFILLED",
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload={
                "obligation_id": obligation.id,
                "action_id": action.id,
                "trigger_message_id": obligation.trigger_message_id,
                "status": obligation.status,
            },
        )
        return obligation

    def _evaluate_sent_resolution_candidate(
        self,
        *,
        reconciliation_id: str,
        action: Action,
        obligation: Obligation,
        message,
    ) -> dict:
        semantic = self.obligation_resolver.evaluate(
            obligation=obligation,
            sent_message=message,
        )
        obligation = self.obligations.note_resolution_candidate(
            obligation=obligation,
            message_id=message.id,
            internal_date_ms=message.internal_date_ms,
            resolves=semantic.resolves,
            confidence=semantic.confidence,
            reason=semantic.reason,
        )

        evidence = {
            "thread_id": message.thread_id,
            "message_id": message.id,
            "subject": message.subject,
            "body_preview": message.body[:2000],
            "internal_date_ms": message.internal_date_ms,
            "semantic_resolution": semantic.model_dump(),
        }

        if not semantic.resolves:
            self.ledger.append(
                source="reconciliator",
                event_type="OBLIGATION_RESOLUTION_CANDIDATE_INSUFFICIENT",
                entity_id=action.source_entity_id,
                correlation_id=reconciliation_id,
                payload={
                    "obligation_id": obligation.id,
                    "action_id": action.id,
                    "candidate_message_id": message.id,
                    **semantic.model_dump(),
                },
            )
            return {
                "correlation_id": reconciliation_id,
                "action_id": action.id,
                "decision": "OBLIGATION_STILL_OPEN",
                "original_action_status": action.initial_status,
                "current_status": action.current_status,
                "obligation": self._obligation_payload(obligation),
                "resolution_candidate": evidence,
                "reason": semantic.reason,
            }

        resolving_action = self.actions.find_by_sent_message_id(message.id)
        if resolving_action:
            resolution_status = "RESOLVED_BY_AGENT"
            event_type = "OBLIGATION_RESOLVED_BY_AGENT"
        else:
            resolution_status = "RESOLVED_BY_USER"
            event_type = "OBLIGATION_RESOLVED_BY_USER"

        resolved_at = datetime.fromtimestamp(message.internal_date_ms / 1000, tz=timezone.utc)
        obligation = self.obligations.resolve(
            obligation=obligation,
            status=resolution_status,
            resolved_at=resolved_at,
            message_id=message.id,
            reason=semantic.reason,
            evidence=evidence,
            resolving_action_id=resolving_action.id if resolving_action else None,
        )
        self.ledger.append(
            source="gmail",
            event_type=event_type,
            entity_id=action.source_entity_id,
            correlation_id=reconciliation_id,
            payload={
                "obligation_id": obligation.id,
                "action_id": action.id,
                "resolution_status": resolution_status,
                "resolving_message_id": message.id,
                "resolving_action_id": resolving_action.id if resolving_action else None,
                "semantic_resolution": semantic.model_dump(),
                "evidence": evidence,
            },
        )
        return {
            "correlation_id": reconciliation_id,
            "action_id": action.id,
            "decision": "OBLIGATION_RESOLVED",
            "original_action_status": action.initial_status,
            "current_status": action.current_status,
            "obligation": self._obligation_payload(obligation),
            "resolution_status": resolution_status,
            "reason": semantic.reason,
            "resolving_message": evidence,
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

    @staticmethod
    def _latest_sent_after_obligation(obligation: Obligation, messages: list):
        context = obligation.context or {}
        trigger_ms = int(context.get("trigger_internal_date_ms") or 0)
        checkpoint_ms = int(context.get("last_resolution_candidate_internal_date_ms") or trigger_ms)
        later_sent = [
            message
            for message in messages
            if message.internal_date_ms > checkpoint_ms and "SENT" in message.labels
        ]
        if not later_sent:
            return None
        return max(later_sent, key=lambda item: item.internal_date_ms)

    @staticmethod
    def _obligation_payload(obligation: Obligation | None) -> dict | None:
        if obligation is None:
            return None
        return {
            "id": obligation.id,
            "type": obligation.obligation_type,
            "status": obligation.status,
            "title": obligation.title,
            "description": obligation.description,
            "trigger_message_id": obligation.trigger_message_id,
            "resolved_at": obligation.resolved_at,
            "resolution": obligation.resolution,
            "context": obligation.context,
        }
