from dataclasses import dataclass
from typing import Literal

from googleapiclient.errors import HttpError

from app.integrations.gmail.client import GmailClient

VerificationStatus = Literal["VERIFIED_SUCCESS", "EXECUTION_FAILED", "UNOBSERVED"]


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    evidence: dict


class GmailVerifier:
    """Narrow deterministic post-condition checker for Gmail sends."""

    def __init__(self, client: GmailClient):
        self.client = client

    def verify_sent_reply(
        self,
        *,
        sent_message_id: str,
        expected_thread_id: str,
        expected_recipient: str,
    ) -> VerificationResult:
        try:
            message = self.client.get_message(sent_message_id)
        except HttpError as exc:
            status_code = getattr(exc.resp, "status", None)
            if status_code == 404:
                return VerificationResult(
                    status="EXECUTION_FAILED",
                    evidence={"reason": "sent_message_not_found", "message_id": sent_message_id},
                )
            return VerificationResult(
                status="UNOBSERVED",
                evidence={"reason": "gmail_api_unavailable", "http_status": status_code},
            )
        except Exception as exc:
            return VerificationResult(
                status="UNOBSERVED",
                evidence={"reason": "verification_error", "error_type": type(exc).__name__},
            )

        sent_label_present = "SENT" in message.labels
        thread_matches = not expected_thread_id or message.thread_id == expected_thread_id
        recipient_matches = expected_recipient.lower() in self.client.addresses(message.to)

        evidence = {
            "sent_message_id": sent_message_id,
            "observed_thread_id": message.thread_id,
            "expected_thread_id": expected_thread_id,
            "sent_label_present": sent_label_present,
            "recipient_matches": recipient_matches,
        }

        if sent_label_present and thread_matches and recipient_matches:
            return VerificationResult(status="VERIFIED_SUCCESS", evidence=evidence)
        return VerificationResult(status="EXECUTION_FAILED", evidence=evidence)
