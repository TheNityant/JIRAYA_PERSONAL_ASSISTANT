import base64
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import Settings
from app.integrations.gmail.auth import load_credentials
from app.integrations.gmail.content import extract_authored_text, source_links
from app.integrations.gmail.html_text import html_to_text
from app.integrations.gmail.models import GmailMessage


class GmailClient:
    def __init__(
        self,
        settings: Settings,
        *,
        interactive_auth: bool = False,
        credentials: Credentials | None = None,
    ):
        self.settings = settings
        resolved_credentials = credentials or load_credentials(settings, interactive=interactive_auth)
        self.service = build("gmail", "v1", credentials=resolved_credentials, cache_discovery=False)

    def get_profile(self) -> dict[str, Any]:
        return self.service.users().getProfile(userId="me").execute()

    def register_watch(self, *, topic_name: str) -> dict[str, Any]:
        if not topic_name:
            raise ValueError("A Pub/Sub topic name is required to register a Gmail watch.")
        # Deliberately watch the whole mailbox rather than only INBOX so Jiraya also
        # observes the user's own SENT replies and can resolve obligations automatically.
        return (
            self.service.users()
            .watch(userId="me", body={"topicName": topic_name})
            .execute()
        )

    def stop_watch(self) -> None:
        self.service.users().stop(userId="me").execute()

    def list_history_message_ids(self, *, start_history_id: str) -> tuple[list[str], str]:
        """Return message IDs added since a Gmail history cursor and the newest cursor."""
        page_token: str | None = None
        message_ids: list[str] = []
        seen: set[str] = set()
        newest_history_id = str(start_history_id)

        while True:
            response = (
                self.service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=str(start_history_id),
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                    maxResults=500,
                )
                .execute()
            )
            newest_history_id = str(response.get("historyId") or newest_history_id)
            for history in response.get("history", []) or []:
                newest_history_id = str(history.get("id") or newest_history_id)
                for added in history.get("messagesAdded", []) or []:
                    message_id = str((added.get("message") or {}).get("id") or "")
                    if message_id and message_id not in seen:
                        seen.add(message_id)
                        message_ids.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return message_ids, newest_history_id

    def list_message_ids(self, *, query: str, max_results: int = 10) -> list[str]:
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [item["id"] for item in response.get("messages", [])]

    def _latest_for_query(self, query: str) -> GmailMessage | None:
        ids = self.list_message_ids(query=query, max_results=10)
        if not ids:
            return None
        messages = [self.get_message(message_id) for message_id in ids]
        return max(messages, key=lambda item: item.internal_date_ms)

    def get_latest_inbox(self) -> GmailMessage | None:
        """Newest incoming inbox message regardless of read state."""
        return self._latest_for_query("in:inbox")

    def get_latest_unread_inbox(self) -> GmailMessage | None:
        """Compatibility helper used by older/manual flows.

        Manual debug controls should continue targeting the newest inbox message even if
        the user opened it in Gmail between receiving it and pressing Debug. The automatic
        runtime consumes exact history message IDs and does not depend on this helper.
        """
        return self.get_latest_inbox()

    def get_thread_messages(self, thread_id: str) -> list[GmailMessage]:
        raw_thread = (
            self.service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        messages = [self._parse_message(raw) for raw in raw_thread.get("messages", [])]
        return sorted(messages, key=lambda item: item.internal_date_ms)

    def get_message(self, message_id: str) -> GmailMessage:
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return self._parse_message(raw)

    def _parse_message(self, raw: dict[str, Any]) -> GmailMessage:
        payload = raw.get("payload", {})
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in payload.get("headers", [])
        }
        sender_name, sender_email = parseaddr(headers.get("from", ""))
        _, reply_to_email = parseaddr(headers.get("reply-to", ""))
        body = self._extract_body(payload)
        if not body:
            body = raw.get("snippet", "")

        display_name = sender_name or (sender_email.split("@")[0] if sender_email else "there")
        return GmailMessage(
            id=raw["id"],
            thread_id=raw.get("threadId", ""),
            sender=sender_email or headers.get("from", ""),
            sender_name=display_name,
            reply_to=reply_to_email or None,
            to=headers.get("to", ""),
            subject=headers.get("subject", "(no subject)"),
            rfc_message_id=headers.get("message-id") or None,
            snippet=raw.get("snippet", ""),
            body=body.strip(),
            labels=tuple(raw.get("labelIds", [])),
            internal_date_ms=int(raw.get("internalDate", 0) or 0),
        )

    def send_reply(self, *, source: GmailMessage, body_text: str) -> dict[str, Any]:
        recipient = source.reply_to or source.sender
        if not recipient:
            raise ValueError("Cannot reply: source message has no usable sender address.")

        subject = source.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        if source.rfc_message_id:
            message["In-Reply-To"] = source.rfc_message_id
            message["References"] = source.rfc_message_id
        message.set_content(body_text)

        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        request_body = {"raw": encoded}
        if source.thread_id:
            request_body["threadId"] = source.thread_id

        return (
            self.service.users()
            .messages()
            .send(userId="me", body=request_body)
            .execute()
        )

    @staticmethod
    def addresses(value: str) -> set[str]:
        return {address.lower() for _, address in getaddresses([value]) if address}

    @classmethod
    def _extract_body(cls, payload: dict[str, Any]) -> str:
        mime_type = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data")

        if data and mime_type == "text/plain":
            return cls._decode(data)

        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in payload.get("parts", []) or []:
            part_type = part.get("mimeType", "")
            part_data = part.get("body", {}).get("data")
            if part_data and part_type == "text/plain":
                plain_parts.append(cls._decode(part_data))
            elif part_data and part_type == "text/html":
                html_parts.append(cls._html_to_text(cls._decode(part_data)))
            elif part.get("parts"):
                nested = cls._extract_body(part)
                if nested:
                    plain_parts.append(nested)

        if plain_parts:
            plain = "\n".join(plain_parts)
            authored = extract_authored_text(plain)
            missing = []
            for part in html_parts:
                for url in source_links(part):
                    if url not in authored and url not in missing:
                        missing.append(url)
            if authored and missing:
                # HTML alternatives can contain button hrefs absent from text/plain.
                # Insert before quoted history so they retain their authored provenance.
                normalized = plain.replace("\r\n", "\n").replace("\r", "\n").strip()
                return authored + "\n\nLinks from email:\n" + "\n".join(missing) + normalized[len(authored):]
            return plain
        if html_parts:
            return "\n".join(html_parts)
        if data:
            decoded = cls._decode(data)
            return cls._html_to_text(decoded) if mime_type == "text/html" else decoded
        return ""

    @staticmethod
    def _decode(value: str) -> str:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).decode("utf-8", errors="replace")

    @staticmethod
    def _html_to_text(value: str) -> str:
        return html_to_text(value)
