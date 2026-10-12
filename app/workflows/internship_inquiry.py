from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.email_agent import EmailAgent
from app.config import Settings
from app.integrations.gmail.client import GmailClient
from app.providers.base import ModelProvider
from app.services.event_ledger import EventLedger
from app.verifier.gmail_verifier import GmailVerifier


class InternshipInquiryWorkflow:
    def __init__(
        self,
        *,
        settings: Settings,
        db: Session,
        provider: ModelProvider,
        gmail: GmailClient,
    ):
        self.settings = settings
        self.ledger = EventLedger(db)
        self.gmail = gmail
        self.agent = EmailAgent(provider)
        self.verifier = GmailVerifier(gmail)

    def run(self, *, message_id: str | None, dry_run: bool = True) -> dict:
        correlation_id = str(uuid4())
        message = self.gmail.get_message(message_id) if message_id else self.gmail.get_latest_unread_inbox()
        if message is None:
            return {
                "correlation_id": correlation_id,
                "decision": "NO_MESSAGE",
                "verification_status": None,
            }

        entity_id = f"gmail:{message.id}"

        # Idempotency guard: once Gmail accepted a reply from Jiraya, never send another
        # reply to the same source message automatically. A failed execution remains retryable.
        if self.ledger.has_event(entity_id=entity_id, event_types=("EMAIL_REPLY_SENT",)):
            self.ledger.append(
                source="policy",
                event_type="EMAIL_SKIPPED_ALREADY_HANDLED",
                entity_id=entity_id,
                correlation_id=correlation_id,
                payload={"message_id": message.id},
            )
            return {
                "correlation_id": correlation_id,
                "message_id": message.id,
                "decision": "ALREADY_HANDLED",
                "verification_status": None,
            }

        self.ledger.append(
            source="gmail",
            event_type="EMAIL_OBSERVED",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload={
                "message_id": message.id,
                "thread_id": message.thread_id,
                "from": message.sender,
                "subject": message.subject,
            },
        )

        classification = self.agent.classify(message)
        self.ledger.append(
            source="agent",
            event_type="EMAIL_CLASSIFIED",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload=classification.model_dump(),
        )

        if (
            classification.label != "INTERNSHIP_INQUIRY"
            or classification.confidence < self.settings.gmail_classification_threshold
        ):
            return {
                "correlation_id": correlation_id,
                "message_id": message.id,
                "classification": classification.model_dump(),
                "decision": "NO_ACTION",
                "verification_status": None,
            }

        reply_body = self._render_reply(message.sender_name)
        self.ledger.append(
            source="agent",
            event_type="EMAIL_REPLY_PROPOSED",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload={"recipient": message.reply_to or message.sender, "template": "internship_reply"},
        )

        if dry_run:
            return {
                "correlation_id": correlation_id,
                "message_id": message.id,
                "classification": classification.model_dump(),
                "decision": "DRY_RUN",
                "proposed_reply": reply_body,
                "verification_status": None,
            }

        if not self.settings.enable_external_actions:
            return {
                "correlation_id": correlation_id,
                "message_id": message.id,
                "classification": classification.model_dump(),
                "decision": "BLOCKED_BY_POLICY",
                "reason": "ENABLE_EXTERNAL_ACTIONS is false",
                "verification_status": None,
            }

        recipient = message.reply_to or message.sender
        try:
            sent = self.gmail.send_reply(source=message, body_text=reply_body)
        except Exception as exc:
            self.ledger.append(
                source="agent",
                event_type="EMAIL_REPLY_EXECUTION_FAILED",
                entity_id=entity_id,
                correlation_id=correlation_id,
                payload={"error_type": type(exc).__name__},
            )
            return {
                "correlation_id": correlation_id,
                "message_id": message.id,
                "classification": classification.model_dump(),
                "decision": "EXECUTION_FAILED",
                "verification_status": "EXECUTION_FAILED",
            }

        sent_message_id = sent["id"]
        self.ledger.append(
            source="gmail",
            event_type="EMAIL_REPLY_SENT",
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload={"sent_message_id": sent_message_id, "thread_id": sent.get("threadId")},
        )

        verification = self.verifier.verify_sent_reply(
            sent_message_id=sent_message_id,
            expected_thread_id=message.thread_id,
            expected_recipient=recipient,
        )
        verification_event = {
            "VERIFIED_SUCCESS": "EMAIL_REPLY_VERIFIED",
            "EXECUTION_FAILED": "EMAIL_REPLY_VERIFICATION_FAILED",
            "UNOBSERVED": "EMAIL_REPLY_UNOBSERVED",
        }[verification.status]
        self.ledger.append(
            source="verifier",
            event_type=verification_event,
            entity_id=entity_id,
            correlation_id=correlation_id,
            payload=verification.evidence,
        )

        return {
            "correlation_id": correlation_id,
            "message_id": message.id,
            "sent_message_id": sent_message_id,
            "classification": classification.model_dump(),
            "decision": "SENT",
            "verification_status": verification.status,
            "verification_evidence": verification.evidence,
        }

    def _render_reply(self, sender_name: str) -> str:
        template_path = Path(self.settings.gmail_reply_template_path)
        if not template_path.exists():
            raise FileNotFoundError(f"Approved reply template not found: {template_path}")
        template = template_path.read_text(encoding="utf-8")
        return template.format(
            sender_name=sender_name or "there",
            signature=self.settings.gmail_reply_signature,
        ).strip()
