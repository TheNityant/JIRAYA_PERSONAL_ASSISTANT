"""Safety gate for real Meet joins. Mirrors the Gmail workflow policy:

- dry_run is the default and never touches a browser.
- Real joins are blocked unless ENABLE_EXTERNAL_ACTIONS=true.
- Links must already have passed link_detector.is_valid_meet_link().
"""

from __future__ import annotations

from dataclasses import dataclass

from app.meet_agent.link_detector import is_valid_meet_link


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str


def evaluate_join_safety(*, dry_run: bool, external_actions_enabled: bool, meet_url: str | None) -> SafetyDecision:
    if not meet_url or not is_valid_meet_link(meet_url):
        return SafetyDecision(False, "INVALID_MEET_LINK")
    if dry_run:
        return SafetyDecision(False, "DRY_RUN")
    if not external_actions_enabled:
        return SafetyDecision(False, "EXTERNAL_ACTIONS_DISABLED")
    return SafetyDecision(True, "ALLOWED")
