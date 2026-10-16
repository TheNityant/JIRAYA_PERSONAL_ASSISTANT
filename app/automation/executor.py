from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.automation.draft_generator import DraftReplyGenerator
from app.automation.matcher import AutomationMatcher
from app.config import Settings
from app.integrations.gmail.client import GmailClient
from app.models import Automation, AutomationExecution
from app.providers.base import ModelProvider
from app.services.action_store import ActionStore
from app.services.automation_execution_store import AutomationExecutionStore
from app.services.event_ledger import EventLedger
from app.verifier.gmail_verifier import GmailVerifier


class AutomationExecutor:
    """Execute persisted user-defined automations against Gmail events."""

    def __init__(
        self,
        *,
        settings: Settings,
        db: Session,
        provider: ModelProvider,
        gmail: GmailClient,
    ):
        self.settings = settings
        self.db = db
        self.provider = provider
        self.gmail = gmail
        self.matcher = AutomationMatcher(provider)
        self.generator = DraftReplyGenerator(provider)
        self.executions = AutomationExecutionStore(db)
        self.actions = ActionStore(db)
        self.ledger = EventLedger(db)
        self.verifier = GmailVerifier(gmail)

    def run(
        self,
        *,
        automation: Automation,
        message_id: str | None = None,
        force: bool = False,
    ) -> dict:
        if not automation.enabled:
            return {"decision": "AUTOMATION_DISABLED", "automation_id": automation.id}
        if automation.source != "gmail" or automation.trigger_type != "NEW_EMAIL":
            return {
                "decision": "UNSUPPORTED_TRIGGER",
                "automation_id": automation.id,
                "source": automation.source,
                "trigger_type": automation.trigger_type,
            }

        message = self.gmail.get_message(message_id) if message_id else self.gmail.get_latest_unread_inbox()
        if message is None:
            return {"decision": "NO_MESSAGE", "automation_id": automation.id}

        match = self.matcher.match_gmail(automation=automation, message=message)
        match_payload = {
            "matches": match.matches,
            "confidence": match.confidence,
            "reason": match.reason,
            "deterministic_checks": match.deterministic_checks,
        }
        if not match.matches:
            return {
                "decision": "NO_MATCH",
                "automation_id": automation.id,
                "message": self._message_payload(message),
                "match": match_payload,
            }

        existing = self.executions.latest_for_event(
            automation_id=automation.id,
            source_event_id=message.id,
        )
        if existing is not None and not force:
            return {
                "decision": "EXISTING_EXECUTION",
                "automation_id": automation.id,
                "message": self._message_payload(message),
                "match": match_payload,
                "execution": self.execution_payload(existing),
            }

        correlation_id = str(uuid4())
        entity_id = f"gmail:{message.id}"
        self.ledger.append(
            source="automation",
            event_type="AUTOMATION_MATCHED",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload={
                "automation_id": automation.id,
                "automation_name": automation.name,
                "message_id": message.id,
                "match": match_payload,
            },
        )

        action_type = str((automation.action_config or {}).get("type") or "DRAFT_REPLY").upper()
        if action_type == "NOTIFY":
            execution = self.executions.create(
                automation_id=automation.id,
                correlation_id=correlation_id,
                source_event_id=message.id,
                source_thread_id=message.thread_id,
                action_type=action_type,
                approval_policy=automation.approval_policy,
                status="NOTIFICATION_READY",
                match_result=match_payload,
                output={
                    "title": automation.name,
                    "message": match.reason,
                    "email_subject": message.subject,
                    "email_from": message.sender,
                },
            )
            self.ledger.append(
                source="automation",
                event_type="AUTOMATION_NOTIFICATION_READY",
                entity_id=entity_id,
                correlation_id=correlation_id,
                payload={"automation_id": automation.id, "execution_id": execution.id},
            )
            return {
                "decision": "NOTIFICATION_READY",
                "automation_id": automation.id,
                "message": self._message_payload(message),
                "match": match_payload,
                "execution": self.execution_payload(execution),
            }

        if action_type != "DRAFT_REPLY":
            return {
                "decision": "UNSUPPORTED_ACTION",
                "automation_id": automation.id,
                "action_type": action_type,
            }

        draft_body = self.generator.generate(automation=automation, message=message)
        if automation.approval_policy == "DRAFT_ONLY":
            status = "DRAFT_READY"
        elif automation.approval_policy == "REQUIRED":
            status = "AWAITING_APPROVAL"
        elif automation.approval_policy == "AUTO":
            status = "AUTO_PENDING"
        else:
            return {
                "decision": "UNSUPPORTED_APPROVAL_POLICY",
                "automation_id": automation.id,
                "approval_policy": automation.approval_policy,
            }

        execution = self.executions.create(
            automation_id=automation.id,
            correlation_id=correlation_id,
            source_event_id=message.id,
            source_thread_id=message.thread_id,
            action_type=action_type,
            approval_policy=automation.approval_policy,
            status=status,
            match_result=match_payload,
            draft_body=draft_body,
            output={
                "recipient": message.reply_to or message.sender,
                "subject": message.subject,
            },
        )
        self.ledger.append(
            source="agent",
            event_type="AUTOMATION_DRAFT_PROPOSED",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload={
                "automation_id": automation.id,
                "execution_id": execution.id,
                "approval_policy": automation.approval_policy,
                "recipient": message.reply_to or message.sender,
            },
        )

        if automation.approval_policy == "AUTO":
            if not self.settings.enable_external_actions:
                execution = self.executions.update(
                    execution,
                    status="BLOCKED_BY_POLICY",
                    output={
                        **(execution.output or {}),
                        "reason": "ENABLE_EXTERNAL_ACTIONS is false",
                    },
                )
                return {
                    "decision": "BLOCKED_BY_POLICY",
                    "automation_id": automation.id,
                    "message": self._message_payload(message),
                    "match": match_payload,
                    "execution": self.execution_payload(execution),
                }
            return self._send(execution=execution, message=message)

        decision = "DRAFT_READY" if automation.approval_policy == "DRAFT_ONLY" else "AWAITING_APPROVAL"
        return {
            "decision": decision,
            "automation_id": automation.id,
            "message": self._message_payload(message),
            "match": match_payload,
            "execution": self.execution_payload(execution),
        }

    def approve(self, *, execution: AutomationExecution, edited_draft: str | None = None) -> dict:
        if execution.action_type != "DRAFT_REPLY":
            return {"decision": "NOT_APPROVABLE", "execution": self.execution_payload(execution)}
        if execution.approval_policy == "DRAFT_ONLY":
            return {"decision": "DRAFT_ONLY", "execution": self.execution_payload(execution)}
        if execution.status in {"VERIFIED_SUCCESS", "EXECUTION_FAILED", "UNOBSERVED"}:
            return {"decision": "ALREADY_EXECUTED", "execution": self.execution_payload(execution)}
        if execution.status not in {"AWAITING_APPROVAL", "BLOCKED_BY_POLICY"}:
            return {"decision": "NOT_APPROVABLE", "execution": self.execution_payload(execution)}
        if not self.settings.enable_external_actions:
            return {
                "decision": "BLOCKED_BY_POLICY",
                "reason": "ENABLE_EXTERNAL_ACTIONS is false",
                "execution": self.execution_payload(execution),
            }

        if edited_draft is not None:
            edited = edited_draft.strip()
            if not edited:
                return {"decision": "EMPTY_DRAFT", "execution": self.execution_payload(execution)}
            execution = self.executions.update(execution, draft_body=edited)

        message = self.gmail.get_message(execution.source_event_id)
        return self._send(execution=execution, message=message)

    def _send(self, *, execution: AutomationExecution, message) -> dict:
        recipient = message.reply_to or message.sender
        entity_id = f"gmail:{message.id}"
        try:
            sent = self.gmail.send_reply(source=message, body_text=execution.draft_body)
        except Exception as exc:
            execution = self.executions.update(
                execution,
                status="EXECUTION_FAILED",
                output={**(execution.output or {}), "error_type": type(exc).__name__},
                executed_at=datetime.now(timezone.utc),
            )
            self.ledger.append(
                source="automation",
                event_type="EMAIL_REPLY_EXECUTION_FAILED",
                entity_id=entity_id,
                correlation_id=execution.correlation_id,
                payload={"execution_id": execution.id, "error_type": type(exc).__name__},
            )
            return {"decision": "EXECUTION_FAILED", "execution": self.execution_payload(execution)}

        sent_event = self.ledger.append(
            source="gmail",
            event_type="EMAIL_REPLY_SENT",
            entity_id=entity_id,
            correlation_id=execution.correlation_id,
            payload={"sent_message_id": sent["id"], "thread_id": sent.get("threadId")},
        )
        verification = self.verifier.verify_sent_reply(
            sent_message_id=sent["id"],
            expected_thread_id=message.thread_id,
            expected_recipient=recipient,
        )
        verification_event = {
            "VERIFIED_SUCCESS": "EMAIL_REPLY_VERIFIED",
            "EXECUTION_FAILED": "EMAIL_REPLY_VERIFICATION_FAILED",
            "UNOBSERVED": "EMAIL_REPLY_UNOBSERVED",
        }[verification.status]
        verified_event = self.ledger.append(
            source="verifier",
            event_type=verification_event,
            entity_id=entity_id,
            correlation_id=execution.correlation_id,
            payload=verification.evidence,
        )
        action = self.actions.create_email_action(
            correlation_id=execution.correlation_id,
            source_entity_id=entity_id,
            source_message_id=message.id,
            sent_message_id=sent["id"],
            thread_id=sent.get("threadId") or message.thread_id,
            recipient=recipient,
            status=verification.status,
            executed_at=sent_event.occurred_at,
            verified_at=verified_event.occurred_at if verification.status == "VERIFIED_SUCCESS" else None,
            verification_evidence=verification.evidence,
        )
        execution = self.executions.update(
            execution,
            status=verification.status,
            output={
                **(execution.output or {}),
                "sent_message_id": sent["id"],
                "thread_id": sent.get("threadId") or message.thread_id,
                "verification": verification.evidence,
            },
            action_id=action.id,
            executed_at=sent_event.occurred_at,
        )
        return {
            "decision": "SENT",
            "verification_status": verification.status,
            "action_id": action.id,
            "execution": self.execution_payload(execution),
        }

    @staticmethod
    def execution_payload(execution: AutomationExecution) -> dict:
        return {
            "id": execution.id,
            "automation_id": execution.automation_id,
            "correlation_id": execution.correlation_id,
            "source": execution.source,
            "source_event_id": execution.source_event_id,
            "source_thread_id": execution.source_thread_id,
            "action_type": execution.action_type,
            "approval_policy": execution.approval_policy,
            "status": execution.status,
            "match_result": execution.match_result,
            "draft_body": execution.draft_body,
            "output": execution.output,
            "action_id": execution.action_id,
            "created_at": execution.created_at,
            "updated_at": execution.updated_at,
            "executed_at": execution.executed_at,
        }

    @staticmethod
    def _message_payload(message) -> dict:
        return {
            "id": message.id,
            "thread_id": message.thread_id,
            "from": message.sender,
            "subject": message.subject,
            "body_preview": message.body[:1500],
        }
