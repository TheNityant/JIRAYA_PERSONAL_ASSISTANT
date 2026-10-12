from dataclasses import dataclass, field


@dataclass(frozen=True)
class GmailMessage:
    id: str
    thread_id: str
    sender: str
    sender_name: str
    reply_to: str | None
    to: str
    subject: str
    rfc_message_id: str | None
    snippet: str
    body: str
    labels: tuple[str, ...] = field(default_factory=tuple)
    internal_date_ms: int = 0
