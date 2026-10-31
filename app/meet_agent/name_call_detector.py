"""Name-call trigger detection for live Meet transcripts.

The owner is spoken to by name in natural conversation. One isolated mention
may just be a passing reference ("Jay sent that yesterday"), but repeated
direct address within a short window usually means someone is waiting for a
response from the owner.

Design (matching the owner's requirement: trigger after 2-3 mentions,
"situation sensitive"):

- Every transcript segment is scanned for the owner's name variants plus
  direct-address cues ("hey Jay", "Jay, tum batao", question marks nearby).
- A direct address (cue word, or name at the start of a question) counts as a
  STRONG mention; a bare name counts as a WEAK mention.
- The trigger fires when the recent window (default 90 s) accumulates either
  >= 2 strong mentions or >= 3 total mentions.
- A cooling period (default 120 s) after each trigger prevents the assistant
  from talking over the conversation it just joined.

This module is pure logic: no audio, no network, fully unit-testable.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

STRONG_CUES = (
    "hey",
    "hello",
    "hi ",
    "batao",
    "bata",
    "bolo",
    "sun",
    "sunno",
    "answer",
    "reply",
    "tum",
    "tumhi",
    "aap",
    "tu",
    "your",
    "you",
    "kya",
    "kuch",
)

QUESTION_MARK = "?"

DEFAULT_WINDOW_SECONDS = 90.0
DEFAULT_STRONG_THRESHOLD = 2
DEFAULT_TOTAL_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 120.0


@dataclass
class Mention:
    text: str
    speaker: str
    strong: bool
    at: float  # monotonic timestamp


@dataclass
class TriggerDecision:
    triggered: bool
    reason: str  # NEVER_TRIGGERED | COOLDOWN | NOT_ENOUGH_MENTIONS | THRESHOLD_REACHED
    strong_count: int = 0
    total_count: int = 0
    mentions: list[Mention] = field(default_factory=list)


class NameCallDetector:
    """Accumulates transcript mentions of the owner and decides when to speak."""

    def __init__(
        self,
        *,
        name_variants: list[str],
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        strong_threshold: int = DEFAULT_STRONG_THRESHOLD,
        total_threshold: int = DEFAULT_TOTAL_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock=time.monotonic,
    ):
        lowered = [variant.strip().lower() for variant in name_variants if variant.strip()]
        if not lowered:
            raise ValueError("At least one name variant is required for name-call detection.")
        self.name_variants = lowered
        self.window_seconds = window_seconds
        self.strong_threshold = strong_threshold
        self.total_threshold = total_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self.mentions: list[Mention] = []
        self.last_triggered_at: float | None = None

    # ------------------------------------------------------------------ scan

    def _name_pattern(self) -> re.Pattern[str]:
        # Word-boundary match so "Jaya" does not match inside "Jayant".
        pattern = r"\b(?:" + "|".join(re.escape(v) for v in self.name_variants) + r")\b"
        return re.compile(pattern, re.IGNORECASE)

    def _is_strong(self, text: str, match: re.Match[str]) -> bool:
        after = text[match.end() : match.end() + 30].lower()
        before = text[max(0, match.start() - 20) : match.start()].lower()
        context = before + after
        if any(cue.strip() in context for cue in STRONG_CUES if cue.strip()):
            return True
        # Name followed closely by a question mark is a direct question.
        return QUESTION_MARK in text[match.end() : match.end() + 80]

    def observe(self, text: str, speaker: str = "unknown") -> Mention | None:
        """Feed one transcript segment. Returns the mention if the name appeared."""
        if not text:
            return None
        pattern = self._name_pattern()
        match = pattern.search(text)
        if not match:
            return None
        mention = Mention(
            text=text,
            speaker=speaker,
            strong=self._is_strong(text, match),
            at=self._clock(),
        )
        self.mentions.append(mention)
        return mention

    # --------------------------------------------------------------- decision

    def _recent(self, now: float) -> list[Mention]:
        cutoff = now - self.window_seconds
        return [m for m in self.mentions if m.at >= cutoff]

    def in_cooldown(self, now: float) -> bool:
        return (
            self.last_triggered_at is not None
            and now - self.last_triggered_at < self.cooldown_seconds
        )

    def evaluate(self) -> TriggerDecision:
        now = self._clock()
        recent = self._recent(now)
        strong = sum(1 for m in recent if m.strong)
        total = len(recent)
        if self.in_cooldown(now):
            return TriggerDecision(False, "COOLDOWN", strong, total, recent)
        if strong >= self.strong_threshold or total >= self.total_threshold:
            self.last_triggered_at = now
            return TriggerDecision(True, "THRESHOLD_REACHED", strong, total, recent)
        return TriggerDecision(False, "NOT_ENOUGH_MENTIONS", strong, total, recent)

    def should_speak(self, text: str, speaker: str = "unknown") -> TriggerDecision:
        """Convenience: observe then evaluate in one call."""
        self.observe(text, speaker)
        return self.evaluate()
