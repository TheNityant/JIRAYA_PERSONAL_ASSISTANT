from __future__ import annotations

from datetime import datetime, timezone

from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.integrations.gmail.connection import get_gmail_client
from app.integrations.gmail.models import GmailMessage
from app.providers.nebius import NebiusProvider
from app.services.action_store import ActionStore
from app.services.automation_store import AutomationStore
from app.services.connection_store import ConnectionStore
from app.services.notification_store import NotificationStore
from app.services.runtime_state_store import RuntimeStateStore
from app.workflows.gmail_reconciliation import GmailReconciliationWorkflow


class GmailRuntimeService:
    """Consume Gmail history and route events into execution/reconciliation."""

    def __init__(self, *, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.gmail = get_gmail_client(db, settings)
        self.provider = NebiusProvider(settings)
        self.state = RuntimeStateStore(db)
        self.notifications = NotificationStore(db)
        self.actions = ActionStore(db)
        self.automations = AutomationStore(db)
        self.executor = AutomationExecutor(
            settings=settings,
            db=db,
            provider=self.provider,
            gmail=self.gmail,
        )

    @property
    def cursor_key(self) -> str:
        return f"gmail-history:{self.settings.dev_user_id}"

    def poll(self) -> dict:
        if self.settings.gmail_pubsub_topic_name and self.settings.gmail_watch_auto_register:
            self.ensure_watch()

        profile = self.gmail.get_profile()
        current_history_id = str(profile.get("historyId") or "")
        email_address = str(profile.get("emailAddress") or "")
        if not current_history_id:
            return {"decision": "NO_HISTORY_ID", "processed": 0}

        state = self.state.get(self.cursor_key)
        if not state or not state.get("history_id"):
            self.state.set(
                self.cursor_key,
                {
                    **(state or {}),
                    "history_id": current_history_id,
                    "email_address": email_address,
                },
            )
            return {
                "decision": "CURSOR_INITIALIZED",
                "processed": 0,
                "history_id": current_history_id,
            }

        if str(state["history_id"]) == current_history_id:
            return {
                "decision": "NO_CHANGE",
                "processed": 0,
                "history_id": current_history_id,
            }

        return self.consume_history(target_history_id=current_history_id, email_address=email_address)

    def ensure_watch(self) -> dict | None:
        """Renew the Gmail watch when it is absent or within 24 hours of expiry."""
        if not self.settings.gmail_pubsub_topic_name:
            return None
        state = self.state.get(self.cursor_key) or {}
        expiration_raw = state.get("watch_expiration")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            expiration_ms = int(expiration_raw or 0)
        except (TypeError, ValueError):
            expiration_ms = 0
        if expiration_ms > now_ms + 24 * 60 * 60 * 1000:
            return None

        watch = self.gmail.register_watch(topic_name=self.settings.gmail_pubsub_topic_name)
        profile = self.gmail.get_profile()
        existing_cursor = str(state.get("history_id") or "")
        watch_history = str(watch.get("historyId") or profile.get("historyId") or "")
        next_state = {
            **state,
            # Never advance a valid processing cursor merely because a watch was renewed.
            "history_id": existing_cursor or watch_history,
            "email_address": profile.get("emailAddress") or state.get("email_address") or "",
            "watch_expiration": watch.get("expiration"),
            "pubsub_topic": self.settings.gmail_pubsub_topic_name,
        }
        self.state.set(self.cursor_key, next_state)

        connection = ConnectionStore(self.db).get_active(
            user_id=self.settings.dev_user_id,
            provider="google",
        )
        if connection:
            ConnectionStore(self.db).update_metadata(
                connection,
                {
                    "gmail_watch_expiration": watch.get("expiration"),
                    "gmail_pubsub_topic": self.settings.gmail_pubsub_topic_name,
                },
            )
        return watch

    def consume_history(self, *, target_history_id: str | None = None, email_address: str | None = None) -> dict:
        state = self.state.get(self.cursor_key)
        if not state or not state.get("history_id"):
            profile = self.gmail.get_profile()
            history_id = str(profile.get("historyId") or target_history_id or "")
            self.state.set(
                self.cursor_key,
                {
                    **(state or {}),
                    "history_id": history_id,
                    "email_address": str(profile.get("emailAddress") or email_address or ""),
                },
            )
            return {"decision": "CURSOR_INITIALIZED", "processed": 0, "history_id": history_id}

        start_history_id = str(state["history_id"])
        try:
            message_ids, newest_history_id = self.gmail.list_history_message_ids(
                start_history_id=start_history_id
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                profile = self.gmail.get_profile()
                reset_id = str(profile.get("historyId") or target_history_id or start_history_id)
                self.state.set(
                    self.cursor_key,
                    {
                        **state,
                        "history_id": reset_id,
                        "email_address": str(profile.get("emailAddress") or email_address or ""),
                        "resynced": True,
                    },
                )
                self.notifications.create_once(
                    user_id=self.settings.dev_user_id,
                    category="INFORMATION",
                    severity="INFO",
                    title="Gmail monitor resynced",
                    message="The Gmail history cursor expired, so Jiraya resumed from the current mailbox state.",
                    source="gmail",
                    dedupe_key=f"gmail-resync:{reset_id}",
                )
                return {"decision": "CURSOR_RESYNCED", "processed": 0, "history_id": reset_id}
            raise

        messages: list[GmailMessage] = []
        for message_id in message_ids:
            try:
                messages.append(self.gmail.get_message(message_id))
            except Exception:
                continue
        messages.sort(key=lambda item: item.internal_date_ms)

        results = [self.process_message(message) for message in messages]
        candidates = [value for value in (newest_history_id, target_history_id) if value]
        try:
            final_history_id = str(max(int(value) for value in candidates)) if candidates else start_history_id
        except ValueError:
            final_history_id = str(newest_history_id or target_history_id or start_history_id)
        self.state.set(
            self.cursor_key,
            {
                **state,
                "history_id": final_history_id,
                "email_address": email_address or state.get("email_address") or "",
            },
        )
        return {
            "decision": "PROCESSED",
            "processed": len(messages),
            "history_id": final_history_id,
            "results": results,
        }

    def process_message(self, message: GmailMessage) -> dict:
        action = self.actions.latest_verified_for_thread(message.thread_id)
        if action is not None and message.id != (action.context or {}).get("source_message_id"):
            reconciliation = GmailReconciliationWorkflow(
                db=self.db,
                provider=self.provider,
                gmail=self.gmail,
            ).run(action_id=action.id)
            self._notify_reconciliation(action_id=action.id, result=reconciliation)
            return {
                "message_id": message.id,
                "thread_id": message.thread_id,
                "path": "RECONCILIATION",
                "result": reconciliation,
            }

        if "SENT" in message.labels:
            return {
                "message_id": message.id,
                "thread_id": message.thread_id,
                "path": "IGNORED_SENT",
            }

        results: list[dict] = []
        reply_claimed = False
        for automation in self.automations.list(source="gmail", enabled=True):
            action_type = str((automation.action_config or {}).get("type") or "DRAFT_REPLY").upper()
            if action_type == "DRAFT_REPLY" and reply_claimed:
                continue

            result = self.executor.run(automation=automation, message_id=message.id)
            results.append({"automation_id": automation.id, "result": result})
            if action_type == "DRAFT_REPLY" and result.get("decision") not in {
                "NO_MATCH",
                "AUTOMATION_DISABLED",
                "UNSUPPORTED_TRIGGER",
            }:
                reply_claimed = True

        return {
            "message_id": message.id,
            "thread_id": message.thread_id,
            "path": "AUTOMATIONS",
            "results": results,
        }

    def _notify_reconciliation(self, *, action_id: str, result: dict) -> None:
        decision = result.get("decision")
        obligation = result.get("obligation") or {}
        if decision == "OBLIGATION_RESOLVED":
            self.notifications.create_once(
                user_id=self.settings.dev_user_id,
                category="STATE_CHANGED",
                severity="SUCCESS",
                title=f"Resolved: {obligation.get('title') or 'Obligation'}",
                message=result.get("reason") or "Jiraya observed evidence that the obligation was completed.",
                source="reconciliator",
                action_id=action_id,
                obligation_id=obligation.get("id"),
                dedupe_key=f"obligation-resolved:{obligation.get('id')}:{result.get('resolution_status')}",
                metadata={"result": decision, "resolution_status": result.get("resolution_status")},
            )
            return

        if decision == "RECONCILED" and result.get("requires_follow_up"):
            classification = result.get("classification") or "NEW_OBLIGATION"
            self.notifications.create_once(
                user_id=self.settings.dev_user_id,
                category="ACTION_REQUIRED",
                severity="WARNING",
                title=obligation.get("title") or "New follow-up required",
                message=result.get("reason") or "A later message changed what needs to happen next.",
                source="reconciliator",
                action_id=action_id,
                obligation_id=obligation.get("id"),
                dedupe_key=f"reconcile:{action_id}:{(result.get('later_message') or {}).get('message_id')}:{classification}",
                metadata={"classification": classification},
            )
