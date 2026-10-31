"""Owns the live meeting assistant session for the local companion server.

The companion server is a process-wide singleton here: one owner, one active
meeting at a time. The manager keeps the assistant, the last actions and the
voice-sample configuration so the HTTP routes stay thin and stateless.
"""

import threading
from dataclasses import replace
from pathlib import Path

from app.meet_agent.meeting_assistant import (
    AssistantConfig,
    MeetingAssistant,
)
from app.meet_agent.name_call_detector import NameCallDetector
from app.meet_agent.speech_to_text import AssemblyAIStream, SpeechToTextUnavailable
from app.meet_agent.reply_generator import ReplyGenerator
from app.meet_agent.voice_engine import (
    DEFAULT_SAMPLE_PATH,
    VoiceConfig,
    VoiceEngine,
    validate_voice_sample,
)


class MeetingManager:
    """Thread-safe façade over one live MeetingAssistant."""

    def __init__(self, *, settings=None):
        self.guard = threading.RLock()
        self._settings = settings
        self._assistant: MeetingAssistant | None = None
        self._voice_config = VoiceConfig(sample_path=DEFAULT_SAMPLE_PATH)
        self._actions: list[dict] = []  # bounded history for the UI
        self._stt: AssemblyAIStream | None = None

    # ------------------------------------------------------------------ state

    def assistant(self) -> MeetingAssistant:
        with self.guard:
            if self._assistant is None:
                from app.config import get_settings
                from app.providers.nebius import NebiusProvider

                settings = self._settings or get_settings()
                class LazyProvider:
                    def complete(self, message, **kwargs):
                        return NebiusProvider(settings).complete(message, **kwargs)
                provider = LazyProvider()
                generator = ReplyGenerator(provider)
                variants = ["owner"]
                assistant = MeetingAssistant(
                    AssistantConfig(
                        owner_name="Owner",
                        auto_speak=False,
                        name_variants=variants,
                        window_seconds=600.0,
                        cooldown_seconds=10.0,
                    ),
                    detector=NameCallDetector(
                        name_variants=variants,
                        window_seconds=600.0,
                        strong_threshold=2,
                        total_threshold=2,
                        cooldown_seconds=10.0,
                    ),
                    generator=generator,
                    voice=VoiceEngine(self._voice_config),
                )
                self._assistant = assistant
            return self._assistant

    def is_active(self) -> bool:
        with self.guard:
            return self._assistant is not None and self._assistant.enabled

    # ------------------------------------------------------------------- feeds

    def ingest(self, text: str, speaker: str = "unknown") -> dict:
        """Feed one transcript turn; records the resulting action."""
        assistant = self.assistant()
        action = assistant.ingest(text, speaker)
        self._record(action)
        # If an END_MEETING action is produced, set the companion flag.
        # Ending browser attendance is an explicit device action, not transcript text.
        return {
            "kind": action.kind,
            "trigger_reason": action.trigger_reason,
            "reply_text": action.reply_text,
            "detail": action.detail,
        }

    def start_speech_to_text(self) -> dict:
        """Start AssemblyAI streaming STT; finalized turns feed ingest()."""
        with self.guard:
            if self._stt is None:
                self._stt = AssemblyAIStream(
                    lambda turn: self.ingest(turn.text, turn.speaker)
                )
            try:
                self._stt.start()
            except (SpeechToTextUnavailable, RuntimeError) as exc:
                return {"started": False, "error": str(exc)}
            return {"started": True, "status": self._stt.status.__dict__}

    def stop_speech_to_text(self) -> dict:
        with self.guard:
            if self._stt is not None:
                self._stt.stop()
            return {"started": False, "status": self._stt.status.__dict__ if self._stt else None}

    def feed_audio(self, frame: bytes) -> dict:
        """Feed one raw PCM chunk (16 kHz mono s16le) into the stream."""
        with self.guard:
            if self._stt is None or not self._stt.is_configured:
                return {"fed": False, "reason": "not configured"}
            self._stt.feed(frame)
            return {"fed": True}

    def request_reply(self, instruction: str) -> dict:
        assistant = self.assistant()
        action = assistant.request_reply(instruction)
        self._record(action)
        return {
            "kind": action.kind,
            "trigger_reason": action.trigger_reason,
            "reply_text": action.reply_text,
            "detail": action.detail,
        }

    # ---------------------------------------------------------------- settings

    def configure(
        self,
        *,
        owner_name: str | None = None,
        name_variants: list[str] | None = None,
        enabled: bool | None = None,
        general_participation: bool | None = None,
    ) -> dict:
        with self.guard:
            config = AssistantConfig(
                owner_name=self.assistant().config.owner_name
                if owner_name is None
                else owner_name,
                name_variants=self.assistant().config.name_variants
                if name_variants is None
                else list(name_variants),
                general_participation=self.assistant().config.general_participation
                if general_participation is None
                else general_participation,
                auto_speak=self.assistant().config.auto_speak,
            )
            assistant = MeetingAssistant(
                config,
                detector=NameCallDetector(
                    name_variants=list(config.name_variants),
                    window_seconds=self.assistant().detector.window_seconds,
                    cooldown_seconds=self.assistant().detector.cooldown_seconds,
                ),
                generator=self.assistant().generator,
                voice=self.assistant().voice,
            )
            assistant.transcript = self.assistant().transcript
            assistant.spoken_replies = self.assistant().spoken_replies
            assistant.enabled = self.assistant().enabled if enabled is None else enabled
            self._assistant = assistant
            return self.status()

    def set_voice_sample(self, path: Path) -> dict:
        """Point the engine at a validated recording of the owner's voice."""
        with self.guard:
            validate_voice_sample(Path(path))
            self._voice_config = replace(
                self._voice_config, sample_path=Path(path), ready=True
            )
            if self._assistant is not None:
                self._assistant.voice = VoiceEngine(self._voice_config)
            return {"saved": True, "sample_path": str(path)}

    # ------------------------------------------------------------------ status

    def status(self) -> dict:
        with self.guard:
            if self._assistant is None:
                return {
                    "active": False,
                    "voice": {
                        "ready": self._voice_config.ready,
                        "sample_path": str(self._voice_config.sample_path),
                    },
                    "actions": [],
                    "stt": self._stt.status.__dict__ if self._stt else None,
                }
            assistant = self._assistant
            return {
                "active": assistant.enabled,
                "owner_name": assistant.config.owner_name,
                "name_variants": assistant.config.name_variants,
                "general_participation": assistant.config.general_participation,
                "auto_speak": assistant.config.auto_speak,
                "voice": assistant.voice.status(),
                "detector": assistant.detector.evaluate().__dict__,
                "spoken_replies": assistant.spoken_replies,
                "transcript_turns": len(assistant.transcript),
                "actions": list(self._actions[-20:]),
                "stt": self._stt.status.__dict__ if self._stt else None,
            }

    # ----------------------------------------------------------------- private

    def _record(self, action) -> None:
        with self.guard:
            self._actions.append(
                {
                    "kind": action.kind,
                    "trigger_reason": action.trigger_reason,
                    "reply_text": action.reply_text,
                    "detail": action.detail,
                }
            )
            del self._actions[:-50]
