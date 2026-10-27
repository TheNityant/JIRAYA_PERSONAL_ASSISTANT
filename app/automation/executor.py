from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.automation.action_router import AutomationActionRouter
from app.automation.draft_generator import DraftReplyGenerator
from app.automation.matcher import AutomationMatcher
from app.automation.obligation_extractor import ObligationExtractor
from app.config import Settings
from app.integrations.gmail.client import GmailClient
from app.models import Automation, AutomationExecution
from app.providers.base import ModelProvider
from app.services.action_store import ActionStore
from app.services.automation_execution_store import AutomationExecutionStore
from app.services.event_ledger import EventLedger
from app.services.notification_store import NotificationStore
from app.services.obligation_store import ObligationStore
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
        self.router = AutomationActionRouter(provider)
        self.generator = DraftReplyGenerator(provider)
        self.obligation_extractor = ObligationExtractor(provider)
        self.executions = AutomationExecutionStore(db)
        self.actions = ActionStore(db)
        self.obligations = ObligationStore(db)
        self.notifications = NotificationStore(db)
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

        message = (
            self.gmail.get_message(message_id)
            if message_id
            else self.gmail.get_latest_unread_inbox()
        )
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
        if existing is not None and existing.status == "VERIFIED_SUCCESS":
            return {
                "decision": "ALREADY_EXECUTED",
                "automation_id": automation.id,
                "message": self._message_payload(message),
                "match": match_payload,
                "execution": self.execution_payload(existing),
            }
        if existing is not None and (not force or (existing.output or {}).get("calendar_booking")):
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
            self.notifications.create_once(
                user_id=self.settings.dev_user_id,
                category="INFORMATION",
                severity="INFO",
                title=automation.name,
                message=f"{message.subject}: {match.reason}",
                source="automation",
                source_entity_id=entity_id,
                automation_id=automation.id,
                dedupe_key=f"notify:{automation.id}:{message.id}",
                metadata={"execution_id": execution.id, "email_from": message.sender},
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

        if action_type in {"CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"}:
            from app.automation.github_action import GitHubAction

            return GitHubAction(self).prepare(
                automation=automation,
                message=message,
                match=match_payload,
                correlation_id=correlation_id,
            )

        if action_type == "CREATE_CALENDAR_EVENT":
            from app.automation.calendar_action import CalendarEventAction

            return CalendarEventAction(self).prepare(
                automation=automation,
                message=message,
                match=match_payload,
                correlation_id=correlation_id,
            )

        if action_type != "DRAFT_REPLY":
            return {
                "decision": "UNSUPPORTED_ACTION",
                "automation_id": automation.id,
                "action_type": action_type,
            }

        effective_policy = automation.approval_policy
        route_payload: dict | None = None
        if automation.approval_policy == "AUTO":
            try:
                route = self.router.route(automation=automation, message=message)
                route_payload = route.model_dump()
            except Exception as exc:
                route_payload = {
                    "decision": "NOTIFY_ONLY",
                    "confidence": 0.0,
                    "reason": f"Action-safety routing failed: {type(exc).__name__}",
                }

            if route_payload["decision"] == "NOTIFY_ONLY":
                execution = self.executions.create(
                    automation_id=automation.id,
                    correlation_id=correlation_id,
                    source_event_id=message.id,
                    source_thread_id=message.thread_id,
                    action_type=action_type,
                    approval_policy="AUTO",
                    status="FALLBACK_NOTIFICATION",
                    match_result=match_payload,
                    output={
                        "recipient": message.reply_to or message.sender,
                        "subject": message.subject,
                        "action_route": route_payload,
                    },
                )
                self.notifications.create_once(
                    user_id=self.settings.dev_user_id,
                    category="ACTION_REQUIRED",
                    severity="WARNING",
                    title=f"Jiraya paused: {automation.name}",
                    message=route_payload["reason"],
                    source="automation",
                    source_entity_id=entity_id,
                    automation_id=automation.id,
                    dedupe_key=f"fallback:{automation.id}:{message.id}",
                    metadata={
                        "execution_id": execution.id,
                        "email_subject": message.subject,
                        "email_from": message.sender,
                        "route": route_payload,
                    },
                )
                return {
                    "decision": "FALLBACK_NOTIFICATION",
                    "automation_id": automation.id,
                    "message": self._message_payload(message),
                    "match": match_payload,
                    "action_route": route_payload,
                    "execution": self.execution_payload(execution),
                }
            if route_payload["decision"] == "REQUIRE_APPROVAL":
                effective_policy = "REQUIRED"

        scheduling = None
        scheduling_config = (automation.action_config or {}).get("calendar_scheduling")
        if isinstance(scheduling_config, dict) and scheduling_config.get("enabled"):
            from app.automation.scheduling_reply import SchedulingReview, prepare_reply
            from app.integrations.calendar.errors import calendar_failure

            try:
                draft_body, scheduling = prepare_reply(
                    automation=automation,
                    message=message,
                    provider=self.provider,
                    db=self.db,
                    settings=self.settings,
                )
            except Exception as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, SchedulingReview)
                    else calendar_failure(exc)[1]
                )
                execution = self.executions.create(
                    automation_id=automation.id,
                    correlation_id=correlation_id,
                    source_event_id=message.id,
                    source_thread_id=message.thread_id,
                    action_type=action_type,
                    approval_policy=effective_policy,
                    status="FALLBACK_NOTIFICATION",
                    match_result=match_payload,
                    output={"reason": reason},
                )
                self._notify_action_required(
                    automation=automation,
                    message=message,
                    execution=execution,
                    title=f"Scheduling needs review: {automation.name}",
                    text=reason,
                    dedupe_key=f"scheduling-review:{execution.id}",
                )
                return {
                    "decision": "FALLBACK_NOTIFICATION",
                    "execution": self.execution_payload(execution),
                }
        else:
            draft_body = self.generator.generate(automation=automation, message=message)
        if effective_policy == "DRAFT_ONLY":
            status = "DRAFT_READY"
        elif effective_policy == "REQUIRED":
            status = "AWAITING_APPROVAL"
        elif effective_policy == "AUTO":
            status = "AUTO_PENDING"
        else:
            return {
                "decision": "UNSUPPORTED_APPROVAL_POLICY",
                "automation_id": automation.id,
                "approval_policy": effective_policy,
            }

        execution = self.executions.create(
            automation_id=automation.id,
            correlation_id=correlation_id,
            source_event_id=message.id,
            source_thread_id=message.thread_id,
            action_type=action_type,
            approval_policy=effective_policy,
            status=status,
            match_result=match_payload,
            draft_body=draft_body,
            output={
                "recipient": message.reply_to or message.sender,
                "subject": message.subject,
                "configured_approval_policy": automation.approval_policy,
                "action_route": route_payload,
                **({"calendar_scheduling": scheduling} if scheduling else {}),
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
                "approval_policy": effective_policy,
                "recipient": message.reply_to or message.sender,
                "action_route": route_payload,
            },
        )

        if effective_policy == "AUTO":
            if not self.settings.enable_external_actions:
                execution = self.executions.update(
                    execution,
                    status="BLOCKED_BY_POLICY",
                    output={
                        **(execution.output or {}),
                        "reason": "ENABLE_EXTERNAL_ACTIONS is false",
                    },
                )
                self._notify_action_required(
                    automation=automation,
                    message=message,
                    execution=execution,
                    title=f"Automatic reply blocked: {automation.name}",
                    text="External actions are disabled, so Jiraya prepared the reply but did not send it.",
                    dedupe_key=f"blocked:{execution.id}",
                )
                return {
                    "decision": "BLOCKED_BY_POLICY",
                    "automation_id": automation.id,
                    "message": self._message_payload(message),
                    "match": match_payload,
                    "execution": self.execution_payload(execution),
                }
            return self._send(execution=execution, message=message)

        if effective_policy == "REQUIRED":
            self._notify_action_required(
                automation=automation,
                message=message,
                execution=execution,
                title=f"Draft ready: {automation.name}",
                text="Jiraya prepared a reply and is waiting for your approval.",
                dedupe_key=f"approval:{execution.id}",
            )
        elif effective_policy == "DRAFT_ONLY":
            self.notifications.create_once(
                user_id=self.settings.dev_user_id,
                category="INFORMATION",
                title=f"Draft prepared: {automation.name}",
                message=f"A draft was prepared for {message.subject}. Draft-only policy prevents sending.",
                source="automation",
                source_entity_id=entity_id,
                automation_id=automation.id,
                dedupe_key=f"draft:{execution.id}",
                metadata={"execution_id": execution.id},
            )

        decision = "DRAFT_READY" if effective_policy == "DRAFT_ONLY" else "AWAITING_APPROVAL"
        return {
            "decision": decision,
            "automation_id": automation.id,
            "message": self._message_payload(message),
            "match": match_payload,
            "action_route": route_payload,
            "execution": self.execution_payload(execution),
        }

    def approve(self, *, execution: AutomationExecution, edited_draft: str | None = None) -> dict:
        if execution.action_type in {"CREATE_GITHUB_ISSUE", "CREATE_GITHUB_BRANCH"}:
            from app.automation.github_action import GitHubAction

            return GitHubAction(self).approve(execution)
        if execution.action_type == "CREATE_CALENDAR_EVENT":
            from app.automation.calendar_action import CalendarEventAction

            return CalendarEventAction(self).approve(execution)
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
            if (execution.output or {}).get(
                "calendar_scheduling"
            ) and edited != execution.draft_body.strip():
                return {
                    "decision": "SCHEDULING_REGENERATION_REQUIRED",
                    "reason": "Regenerate a scheduling reply instead of editing its validated times.",
                    "execution": self.execution_payload(execution),
                }
            execution = self.executions.update(execution, draft_body=edited)

        message = self.gmail.get_message(execution.source_event_id)
        return self._send(execution=execution, message=message)

    def _send(self, *, execution: AutomationExecution, message) -> dict:
        recipient = message.reply_to or message.sender
        entity_id = f"gmail:{message.id}"
        automation = self.db.get(Automation, execution.automation_id)
        scheduling = (execution.output or {}).get("calendar_scheduling")
        if scheduling:
            from app.automation.scheduling_reply import SchedulingReview, policy_for, recheck
            from app.integrations.calendar.errors import calendar_failure

            try:
                current = policy_for(automation) if automation else None
                if current is None or current.model_dump() != scheduling["policy"]:
                    raise SchedulingReview("Scheduling settings changed. Regenerate the reply.")
                if scheduling.get("booking_candidate"):
                    from app.automation.scheduling_booking import book_before_reply
                    execution = book_before_reply(self, execution, message, scheduling)
                else:
                    recheck(scheduling, self.db, self.settings)
            except Exception as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, SchedulingReview)
                    else calendar_failure(exc)[1]
                )
                execution = self.executions.update(
                    execution,
                    status="FALLBACK_NOTIFICATION",
                    output={**(execution.output or {}), "reason": reason},
                )
                if automation:
                    self._notify_action_required(
                        automation=automation,
                        message=message,
                        execution=execution,
                        title=f"Scheduling needs review: {automation.name}",
                        text=reason,
                        dedupe_key=f"scheduling-stale:{execution.id}",
                    )
                return {
                    "decision": "FALLBACK_NOTIFICATION",
                    "execution": self.execution_payload(execution),
                }
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
            if automation:
                self._notify_action_required(
                    automation=automation,
                    message=message,
                    execution=execution,
                    title=f"Reply failed: {automation.name}",
                    text=("Calendar was booked, but the email reply failed or is unverified. Check the event and Sent folder before retrying." if (execution.output or {}).get("calendar_booking") else "Jiraya could not send the configured reply. Review the connection and try again."),
                    dedupe_key=f"send-failed:{execution.id}",
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
            verified_at=verified_event.occurred_at
            if verification.status == "VERIFIED_SUCCESS"
            else None,
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

        obligation = None
        if verification.status == "VERIFIED_SUCCESS":
            if automation:
                self.notifications.create_once(
                    user_id=self.settings.dev_user_id,
                    category="AUTOMATION_EXECUTED",
                    severity="SUCCESS",
                    title=f"Reply sent: {automation.name}",
                    message=f"Jiraya replied to {recipient} about {message.subject} and Gmail verified the send.",
                    source="automation",
                    source_entity_id=entity_id,
                    automation_id=automation.id,
                    action_id=action.id,
                    dedupe_key=f"sent:{execution.id}",
                    metadata={"execution_id": execution.id, "sent_message_id": sent["id"]},
                )
            try:
                extracted = self.obligation_extractor.extract(message=message)
                if extracted.creates_obligation:
                    obligation = self.obligations.create_from_source_email(
                        action=action,
                        message_id=message.id,
                        thread_id=message.thread_id,
                        internal_date_ms=message.internal_date_ms,
                        title=extracted.title,
                        description=extracted.description,
                        confidence=extracted.confidence,
                    )
                    self.notifications.create_once(
                        user_id=self.settings.dev_user_id,
                        category="ACTION_REQUIRED",
                        severity="WARNING",
                        title=obligation.title,
                        message=obligation.description
                        or "This email created work that remains outstanding.",
                        source="obligation",
                        source_entity_id=entity_id,
                        automation_id=automation.id if automation else None,
                        action_id=action.id,
                        obligation_id=obligation.id,
                        dedupe_key=f"obligation-open:{obligation.id}",
                        metadata={"status": obligation.status},
                    )
            except Exception:
                # Obligation extraction is helpful but must never turn a verified send into a failure.
                obligation = None
        elif automation:
            self._notify_action_required(
                automation=automation,
                message=message,
                execution=execution,
                title=f"Reply needs verification: {automation.name}",
                text=f"Gmail send verification returned {verification.status}.",
                dedupe_key=f"verification:{execution.id}",
            )

        return {
            "decision": "SENT",
            "verification_status": verification.status,
            "action_id": action.id,
            "obligation_id": obligation.id if obligation else None,
            "execution": self.execution_payload(execution),
        }

    def _notify_action_required(
        self,
        *,
        automation: Automation,
        message,
        execution: AutomationExecution,
        title: str,
        text: str,
        dedupe_key: str,
    ) -> None:
        self.notifications.create_once(
            user_id=self.settings.dev_user_id,
            category="ACTION_REQUIRED",
            severity="WARNING",
            title=title,
            message=text,
            source="automation",
            source_entity_id=f"gmail:{message.id}",
            automation_id=automation.id,
            action_id=execution.action_id,
            dedupe_key=dedupe_key,
            metadata={
                "execution_id": execution.id,
                "email_subject": message.subject,
                "email_from": message.sender,
            },
        )

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
