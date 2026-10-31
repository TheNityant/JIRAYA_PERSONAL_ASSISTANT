"""Deterministic Meet join verification.

Same philosophy as the Gmail verifier: no LLM judgement, only source-of-record
facts. The joiner's JoinResult already carries page-derived evidence; this
module turns it into a ledger verdict.

    JOINED   -> MEET_JOIN_VERIFIED
    KNOCKING -> MEET_JOIN_KNOCKING (legitimate state, host must admit)
    FAILED   -> MEET_JOIN_FAILED
    WAITING_FOR_AUTH -> MEET_JOIN_AUTH_REQUIRED (user action needed)
"""

from __future__ import annotations

from dataclasses import dataclass

VERDICTS = {
    "JOINED": "MEET_JOIN_VERIFIED",
    "KNOCKING": "MEET_JOIN_KNOCKING",
    "FAILED": "MEET_JOIN_FAILED",
    "WAITING_FOR_AUTH": "MEET_JOIN_AUTH_REQUIRED",
}


@dataclass(frozen=True)
class Verification:
    verdict: str
    verified: bool


def verify_join(status: str, reason: str, evidence_notes: list[str] | None = None) -> Verification:
    notes = evidence_notes or []
    if status == "JOINED":
        # Cross-check: an in-call claim must have supporting page evidence.
        if "in_call_ui_visible" in notes:
            return Verification("MEET_JOIN_VERIFIED", True)
        return Verification("MEET_JOIN_FAILED", False)
    if status == "KNOCKING" and "knocking_wait_for_host" in notes:
        return Verification("MEET_JOIN_KNOCKING", False)
    if status == "WAITING_FOR_AUTH":
        return Verification("MEET_JOIN_AUTH_REQUIRED", False)
    return Verification(VERDICTS.get(status, "MEET_JOIN_FAILED"), False)
