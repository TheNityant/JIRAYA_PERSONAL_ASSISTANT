"""Meeting assistant orchestrator: transcript in, cloned-voice reply out.

Pipeline (the dots connected):

  transcript segments -> NameCallDetector (2-3 mentions -> trigger)
                      -> ReplyGenerator (Nebius LLM, transcript context)
                      -> VoiceEngine (XTTS clone of the owner's sample)
                      -> audio file (+ chat text) for Meet delivery

General participation mode: when enabled, the detector trigger is bypassed and
the owner can ask for a reply explicitly (e.g. via the chat command channel).

The orchestrator never blocks the caller: synthesis is synchronous here but
short; the caller (API route / extension bridge) decides playback. All state
that persists stays free of audio, tokens, and transcript content.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.meet_agent.name_call_detector import NameCallDetector, TriggerDecision
from app.meet_agent.reply_generator import ReplyGenerator
from app.meet_agent.voice_engine import VoiceEngine


@dataclass
class AssistantAction:
    kind: str  # "SPOKE" | "IGNORED" | "ERROR"
    trigger_reason: str
    reply_text: str = ""
    audio_path: str = ""
    detail: str = ""


@dataclass
class AssistantConfig:
    owner_name: str = "Owner"
    name_variants: list[str] = field(default_factory=lambda: ["owner"])
    auto_speak: bool = True  # False => draft replies but require manual approval
    general_participation: bool = False  # True => allow explicit non-name requests
    window_seconds: float = 90.0
    cooldown_seconds: float = 120.0


class MeetingAssistant:
    """Thread-safe orchestrator for one live meeting."""

    def __init__(
        self,
        config: AssistantConfig,
        detector: NameCallDetector,
        generator: ReplyGenerator,
        voice: VoiceEngine,
    ):
        self.config = config
        self.detector = detector
        self.generator = generator
        self.voice = voice
        self.transcript: list[dict] = []
        self.spoken_replies: int = 0
        self.enabled: bool = True  # master switch (full stop)
        self.guard = threading.RLock()
        self.pending_request: str | None = None  # general-participation channel

    # -------------------------------------------------------------- ingestion

    def ingest(self, text: str, speaker: str = "unknown") -> AssistantAction:
        """Feed one transcript segment; may produce a full spoken action."""
        if not self.enabled:
            return AssistantAction("IGNORED", "DISABLED")
        with self.guard:
            self.transcript.append({"speaker": speaker, "text": text, "at": time.time()})
            decision: TriggerDecision | None = None
            explicit_request = False
            if self.config.general_participation and self.pending_request:
                explicit_request = True
                self.pending_request = None
            elif self.config.auto_speak:
                decision = self.detector.should_speak(text, speaker)
        # --- End‑meeting keyword detection ---
        lower = text.lower()
        if any(kw in lower for kw in ["meeting is over", "meeting ended", "end meeting", "close meeting"]):
            return AssistantAction("END_MEETING", "END_MEETING_DETECTED")
        if explicit_request:
            return self._respond("GENERAL_PARTICIPATION")
        if decision is None or not decision.triggered:
            reason = decision.reason if decision else "AUTO_SPEAK_DISABLED"
            return AssistantAction("IGNORED", reason)
        return self._respond(decision.reason)

    def request_reply(self, instruction: str) -> AssistantAction:
        """Owner-initiated request (general participation mode)."""
        with self.guard:
            if not self.config.general_participation:
                return AssistantAction("IGNORED", "GENERAL_PARTICIPATION_DISABLED")
            self.pending_request = instruction
        return self.ingest("", speaker="owner-request")

    # ---------------------------------------------------------------- speaking

    def _respond(self, trigger_reason: str) -> AssistantAction:
        with self.guard:
            tail = [
                {"speaker": t["speaker"], "text": t["text"]} for t in self.transcript[-12:]
            ]
            first = self.spoken_replies == 0
            try:
                reply = self.generator.generate(
                    transcript_tail=tail,
                    owner_name=self.config.owner_name,
                    first_reply=first,
                )
                audio = self.voice.speak(reply)
            except (ValueError, RuntimeError, OSError) as exc:
                return AssistantAction("ERROR", trigger_reason, detail=str(exc))
            self.spoken_replies += 1
            return AssistantAction(
                "SPOKE",
                trigger_reason,
                reply_text=reply,
                audio_path=str(Path(audio)),
            )

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        with self.guard:
            return {
                "enabled": self.enabled,
                "auto_speak": self.config.auto_speak,
                "general_participation": self.config.general_participation,
                "voice_ready": self.voice.is_ready(),
                "spoken_replies": self.spoken_replies,
                "transcript_turns": len(self.transcript),
                "detector": {
                    "strong_recent": sum(
                        1 for m in self.detector._recent(self.detector._clock()) if m.strong
                    ),
                    "recent": len(self.detector._recent(self.detector._clock())),
                    "cooldown": self.detector.in_cooldown(self.detector._clock()),
                },
            }

    def stop(self) -> None:
        with self.guard:
            self.enabled = False
