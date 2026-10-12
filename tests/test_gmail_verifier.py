from app.integrations.gmail.models import GmailMessage
from app.verifier.gmail_verifier import GmailVerifier


class FakeGmailClient:
    @staticmethod
    def addresses(value: str) -> set[str]:
        return {value.lower()}

    def get_message(self, message_id: str) -> GmailMessage:
        return GmailMessage(
            id=message_id,
            thread_id="thread-1",
            sender="me@example.com",
            sender_name="Me",
            reply_to=None,
            to="recruiter@example.com",
            subject="Re: Internship",
            rfc_message_id="<sent@example.com>",
            snippet="",
            body="Thanks",
            labels=("SENT",),
        )


def test_verifier_accepts_authoritative_sent_evidence():
    result = GmailVerifier(FakeGmailClient()).verify_sent_reply(
        sent_message_id="sent-1",
        expected_thread_id="thread-1",
        expected_recipient="recruiter@example.com",
    )
    assert result.status == "VERIFIED_SUCCESS"
    assert result.evidence["sent_label_present"] is True
