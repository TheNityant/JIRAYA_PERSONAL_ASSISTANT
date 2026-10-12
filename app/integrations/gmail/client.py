import base64
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any

from googleapiclient.discovery import build

from app.config import Settings
from app.integrations.gmail.auth import load_credentials
from app.integrations.gmail.models import GmailMessage


class GmailClient:
    def __init__(self, settings: Settings, *, interactive_auth: bool = False):
        self.settings = settings
        credentials = load_credentials(settings, interactive=interactive_auth)
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def get_profile(self) -> dict[str, Any]:
        return self.service.users().getProfile(userId="me").execute()

    def list_message_ids(self, *, query: str, max_results: int = 10) -> list[str]:
        response = (
            self.service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return [item["id"] for item in response.get("messages", [])]

    def get_latest_unread_inbox(self) -> GmailMessage | None:
        ids = self.list_message_ids(query="is:unread in:inbox", max_results=10)
        if not ids:
            return None
        messages = [self.get_message(message_id) for message_id in ids]
        return max(messages, key=lambda item: item.internal_date_ms)

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
            return "\n".join(plain_parts)
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
        value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
        value = re.sub(r"</p\s*>", "\n", value, flags=re.IGNORECASE)
        value = re.sub(r"<[^>]+>", "", value)
        return html.unescape(value)
