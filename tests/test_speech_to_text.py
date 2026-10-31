"""AssemblyAI speech-to-text integration tests (no network, no SDK required)."""

import pytest

from app.meet_agent.manager import MeetingManager
from app.meet_agent.speech_to_text import AssemblyAIStream, SpeechToTextUnavailable


class FakeProvider:
    provider_name = "fake"

    def complete(self, *args, **kwargs):
        return "I will follow up after the call."


@pytest.fixture(autouse=True)
def _no_assemblyai_key(monkeypatch):
    """Isolate tests from the developer's real .env key."""
    import app.meet_agent.speech_to_text as stt_module

    class _Settings:
        assemblyai_api_key = ""
        assemblyai_sample_rate = 16000

    monkeypatch.setattr(stt_module, "get_settings", lambda: _Settings())


def _manager_without_key():
    manager = MeetingManager()
    stream = AssemblyAIStream(lambda turn: None, api_key="")
    return manager, stream


def test_stream_requires_api_key():
    _, stream = _manager_without_key()
    assert stream.is_configured is False
    try:
        stream.start()
    except SpeechToTextUnavailable as exc:
        assert "ASSEMBLYAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected SpeechToTextUnavailable")


def test_manager_start_reports_missing_key():
    manager = MeetingManager()
    result = manager.start_speech_to_text()
    assert result["started"] is False
    assert "ASSEMBLYAI_API_KEY" in result["error"]


def test_status_snapshot_includes_stt_when_created():
    manager = MeetingManager()
    manager.start_speech_to_text()  # fails to start, but lazily creates the stream
    stt = manager.status()["stt"]
    assert stt is not None
    assert stt["running"] is False
