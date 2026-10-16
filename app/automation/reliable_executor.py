from datetime import datetime, timezone

from app.automation.executor import AutomationExecutor
from app.models import AutomationExecution
from app.reconciler.obligation_resolver import ObligationResolver


class ReliableAutomationExecutor(AutomationExecutor):
    """Automation executor with immediate post-send obligation settlement.

    The base executor extracts durable work from the source email after a verified send.
    Some requests, however, are satisfied by the reply Jiraya just sent (for example an
    update request). Leaving those OPEN until a later Gmail-history cycle is misleading.
    This executor immediately checks the verified sent message against any newly-created
    obligation and closes it when the sent answer itself satisfied the request.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.obligation_resolver = ObligationResolver(self.provider)

    def _send(self, *, execution: AutomationExecution, message) -> dict:
        result = super()._send(execution=execution, message=message)
        if result.get("verification_status") != "VERIFIED_SUCCESS":
            return result

        obligation_id = result.get("obligation_id")
        if not obligation_id:
            return result

        obligation = self.obligations.get(obligation_id)
        if obligation is None or obligation.status != "OPEN":
            return result

        execution_payload = result.get("execution") or {}
        output = execution_payload.get("output") or {}
        sent_message_id = output.get("sent_message_id")
        if not sent_message_id:
            return result

        try:
            sent_message = self.gmail.get_message(sent_message_id)
            semantic = self.obligation_resolver.evaluate(
                obligation=obligation,
                sent_message=sent_message,
            )
        except Exception:
            # A failed semantic post-check must never rewrite a verified send. The open
            # obligation remains available for the normal Gmail-history reconciliator.
            return result

        obligation = self.obligations.note_resolution_candidate(
            obligation=obligation,
            message_id=sent_message.id,
            internal_date_ms=sent_message.internal_date_ms,
            resolves=semantic.resolves,
            confidence=semantic.confidence,
            reason=semantic.reason,
        )
        if not semantic.resolves:
            return result

        resolved_at = datetime.fromtimestamp(
            sent_message.internal_date_ms / 1000,
            tz=timezone.utc,
        )
        action_id = result.get("action_id")
        obligation = self.obligations.resolve(
            obligation=obligation,
            status="RESOLVED_BY_AGENT",
            resolved_at=resolved_at,
            message_id=sent_message.id,
            reason=semantic.reason,
            evidence={
                "thread_id": sent_message.thread_id,
                "message_id": sent_message.id,
                "subject": sent_message.subject,
                "body_preview": sent_message.body[:2000],
                "internal_date_ms": sent_message.internal_date_ms,
                "semantic_resolution": semantic.model_dump(),
            },
            resolving_action_id=action_id,
        )
        self.notifications.mark_obligation_notifications_read(
            user_id=self.settings.dev_user_id,
            obligation_id=obligation.id,
        )
        self.notifications.create_once(
            user_id=self.settings.dev_user_id,
            category="STATE_CHANGED",
            severity="SUCCESS",
            title=f"Resolved automatically: {obligation.title}",
            message=semantic.reason,
            source="reconciliator",
            source_entity_id=obligation.source_entity_id,
            automation_id=execution.automation_id,
            action_id=action_id,
            obligation_id=obligation.id,
            dedupe_key=f"obligation-resolved-immediate:{obligation.id}:{sent_message.id}",
            metadata={
                "resolution_status": "RESOLVED_BY_AGENT",
                "sent_message_id": sent_message.id,
            },
        )
        self.ledger.append(
            source="reconciliator",
            event_type="OBLIGATION_RESOLVED_BY_AGENT",
            entity_id=obligation.source_entity_id,
            correlation_id=execution.correlation_id,
            payload={
                "obligation_id": obligation.id,
                "action_id": action_id,
                "resolving_message_id": sent_message.id,
                "semantic_resolution": semantic.model_dump(),
            },
        )
        result["obligation_status"] = obligation.status
        result["obligation_resolution"] = semantic.model_dump()
        return result
