"""Tests for the live meeting assistant: name trigger, reply, cloned voice.

All heavy components (LLM provider, XTTS model, audio playback) are faked so
these tests never need a model download or a microphone.
"""

import threading
import time

import pytest

from app.meet_agent.meeting_assistant import AssistantConfig, MeetingAssistant
from app.meet_agent.name_call_detector import NameCallDetector
from app.meet_agent.reply_generator import ReplyGenerator
from app.meet_agent.voice_engine import (
    VoiceConfig,
    VoiceEngine,
    VoiceSampleProblem,
    validate_voice_sample,
)


class _StubPath:
    def __fspath__(self):
        return "stub.wav"

    def __str__(self):
        return "stub.wav"


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class FakeProvider:
    """Stands in for the Nebius provider."""

    def __init__(self, reply: str = "Yes, I can take that question."):
        self.reply = reply
        self.prompts: list[str] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake-model"

    def complete(self, message: str, *, enable_thinking: bool = True) -> str:
        self.prompts.append(message)
        return self.reply


class FakeVoice:
    """Stands in for the XTTS VoiceEngine."""

    def __init__(self, ready: bool = True):
        self.ready = ready
        self.spoken: list[str] = []

    def is_ready(self) -> bool:
        return self.ready

    def status(self) -> dict:
        return {"ready": self.ready}

    def speak(self, text: str, *, filename: str | None = None):
        self.spoken.append(text)
        return _StubPath()


def _detector(clock, variants=("Jay",)):
    return NameCallDetector(
        name_variants=list(variants),
        clock=clock,
        cooldown_seconds=60.0,
        window_seconds=90.0,
    )


def _assistant(clock, provider=None, voice=None, config=None):
    provider = provider or FakeProvider()
    voice = voice or FakeVoice()
    config = config or AssistantConfig(owner_name="Jay", name_variants=["Jay"])
    generator = ReplyGenerator(provider)
    detector = _detector(clock)
    return MeetingAssistant(config, detector, generator, voice), provider, voice


# ------------------------------------------------------------------ detector


def test_single_weak_mention_does_not_trigger():
    clock = FakeClock()
    detector = _detector(clock)
    assert detector.should_speak("Jay sent that file yesterday", "A").triggered is False


def test_two_strong_direct_addresses_trigger():
    clock = FakeClock()
    detector = _detector(clock)
    assert detector.should_speak("Hey Jay, can you share the numbers?", "A").triggered is False
    assert detector.should_speak("Jay, tum answer do na", "B").triggered is True


def test_three_total_mentions_trigger_even_if_weak():
    clock = FakeClock()
    detector = _detector(clock)
    detector.should_speak("Jay was on leave", "A")
    detector.should_speak("Ask Jay about the budget", "B")
    decision = detector.should_speak("Jay's team did that", "C")
    assert decision.triggered is True
    assert decision.reason == "THRESHOLD_REACHED"
    assert decision.total_count == 3


def test_old_mentions_fall_out_of_the_window():
    clock = FakeClock()
    detector = _detector(clock)
    detector.should_speak("Hey Jay, you there?", "A")
    clock.advance(300)  # beyond the 90 s window
    decision = detector.should_speak("Jay, one more question", "B")
    assert decision.total_count == 1
    assert decision.triggered is False


def test_cooldown_suppresses_immediate_retrigger():
    clock = FakeClock()
    detector = _detector(clock)
    first = detector.should_speak("Hey Jay, your update?", "A")
    assert first.triggered is False  # one mention alone is not enough
    second = detector.should_speak("Jay, you still there?", "B")
    assert second.triggered is True  # second strong mention crosses the threshold
    clock.advance(10)
    third = detector.should_speak("Jay please answer", "A")
    assert third.triggered is False
    assert third.reason == "COOLDOWN"
    clock.advance(120)
    assert detector.should_speak("Jay please answer", "A").triggered is False  # fresh window
    assert detector.should_speak("Jay, one more update?", "A").triggered is True


def test_name_variant_requires_word_boundary():
    clock = FakeClock()
    detector = _detector(clock, variants=("Jay",))
    detector.should_speak("Jayant handled it", "A")
    assert detector.evaluate().total_count == 0


def test_empty_name_variants_rejected():
    with pytest.raises(ValueError):
        NameCallDetector(name_variants=[])


# -------------------------------------------------------------- voice sample


def _write_wav(path, seconds: float, rate: int = 16000, channels: int = 1):
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x02" * int(rate * seconds))


def test_voice_sample_too_short_is_rejected(tmp_path):
    sample = tmp_path / "sample.wav"
    _write_wav(sample, 5)
    with pytest.raises(VoiceSampleProblem, match="too short"):
        validate_voice_sample(sample)


def test_voice_sample_valid_within_limits(tmp_path):
    sample = tmp_path / "sample.wav"
    _write_wav(sample, 30)
    assert validate_voice_sample(sample) == pytest.approx(30, abs=0.2)


def test_voice_sample_missing_or_corrupt(tmp_path):
    with pytest.raises(VoiceSampleProblem, match="not found"):
        validate_voice_sample(tmp_path / "missing.wav")
    corrupt = tmp_path / "bad.wav"
    corrupt.write_text("not a wav")
    with pytest.raises(VoiceSampleProblem):
        validate_voice_sample(corrupt)


def test_voice_engine_not_ready_without_sample(tmp_path):
    engine = VoiceEngine(VoiceConfig(sample_path=tmp_path / "none.wav"))
    assert engine.is_ready() is False


def test_voice_engine_speak_requires_valid_sample(tmp_path):
    engine = VoiceEngine(VoiceConfig(sample_path=tmp_path / "none.wav"))
    with pytest.raises(VoiceSampleProblem):
        engine.speak("hello")


# ------------------------------------------------------------ reply generator


def test_reply_generator_uses_provider_and_limits_context():
    provider = FakeProvider("I will follow up after the call.")
    generator = ReplyGenerator(provider)
    tail = [{"speaker": f"S{i}", "text": f"turn {i}"} for i in range(15)]
    reply = generator.generate(transcript_tail=tail, owner_name="Jay", first_reply=True)
    assert reply == "I will follow up after the call."
    assert "Jay" in provider.prompts[0]
    # Only the last 10 turns are included.
    assert "turn 14" in provider.prompts[0]
    assert "turn 0" not in provider.prompts[0]


def test_reply_generator_requires_context():
    generator = ReplyGenerator(FakeProvider())
    with pytest.raises(ValueError):
        generator.generate(transcript_tail=[], owner_name="Jay", first_reply=False)


# ------------------------------------------------------------------- pipeline


def test_pipeline_ignores_until_threshold_then_speaks():
    clock = FakeClock()
    assistant, provider, voice = _assistant(clock)
    first = assistant.ingest("Hey Jay, you there?", "A")
    assert first.kind == "IGNORED"
    second = assistant.ingest("Jay, please answer the deployment question", "B")
    assert second.kind == "SPOKE"
    assert assistant.spoken_replies == 1
    assert len(voice.spoken) == 1
    assert "first reply" in provider.prompts[0] or "introduction" in provider.prompts[0]
    # Cooldown: immediately after, mentions are ignored.
    third = assistant.ingest("Jay? Jay?", "A")
    assert third.kind == "IGNORED"
    assert third.trigger_reason == "COOLDOWN"


def test_disabled_assistant_never_speaks():
    clock = FakeClock()
    assistant, _, voice = _assistant(clock)
    assistant.stop()
    action = assistant.ingest("Hey Jay, hello?", "A")
    assert action.kind == "IGNORED"
    assert action.trigger_reason == "DISABLED"
    assert voice.spoken == []


def test_voice_not_ready_produces_error_action():
    clock = FakeClock()
    assistant, _, _ = _assistant(clock, voice=FakeVoice(ready=False))
    assistant.ingest("Hey Jay, your answer?", "A")
    action = assistant.ingest("Jay, are you muted?", "B")
    assert action.kind == "SPOKE" or action.kind == "ERROR"
    if action.kind == "ERROR":
        assert action.detail


def test_general_participation_explicit_request(tmp_path):
    clock = FakeClock()
    config = AssistantConfig(
        owner_name="Jay",
        name_variants=["Jay"],
        general_participation=True,
    )
    assistant, provider, voice = _assistant(clock, config=config)
    action = assistant.request_reply("Please tell them the build is green")
    assert action.kind == "SPOKE"
    assert voice.spoken == [provider.reply or action.reply_text]
    assert assistant.ingest("no mention here", "A").kind == "IGNORED"


def test_general_participation_disabled_by_default():
    clock = FakeClock()
    assistant, _, _ = _assistant(clock)
    assert assistant.request_reply("speak now").kind == "IGNORED"


def test_status_reports_counts():
    clock = FakeClock()
    assistant, _, _ = _assistant(clock)
    assistant.ingest("Hey Jay, you there?", "A")
    status = assistant.status()
    assert status["detector"]["recent"] == 1
    assert status["spoken_replies"] == 0
    assert status["voice_ready"] is True


def test_concurrent_ingest_is_thread_safe():
    clock = FakeClock()
    assistant, _, _ = _assistant(clock)
    errors: list[Exception] = []

    def worker(index: int):
        try:
            for i in range(20):
                assistant.ingest(f"message {index}-{i}", f"speaker{index}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert assistant.status()["transcript_turns"] == 100
