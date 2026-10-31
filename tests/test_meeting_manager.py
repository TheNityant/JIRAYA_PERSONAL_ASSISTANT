"""Tests for the meeting manager and companion routes wiring."""

import pytest
from fastapi.testclient import TestClient

from app.local_companion.server import create_companion
from app.meet_agent.manager import MeetingManager
from app.meet_agent.voice_engine import VoiceSampleProblem


def _write_wav(path, seconds: float, rate: int = 16000):
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x02" * int(rate * seconds))


def _token(client) -> str:
    return client.app.state.companion.config["token"]


def _headers(client) -> dict:
    return {"Authorization": f"Bearer {_token(client)}"}


# ------------------------------------------------------------------- manager


def test_manager_starts_inactive_and_lazily_creates_assistant():
    manager = MeetingManager()
    assert manager.is_active() is False
    status = manager.status()
    assert status["active"] is False
    assert status["voice"]["ready"] is False


def test_manager_ingest_records_action_history():
    manager = MeetingManager()
    manager.ingest("Hello everyone", "A")
    result = manager.ingest("Hey owner, can you answer?", "B")
    assert result["kind"] in {"SPOKE", "IGNORED", "ERROR"}
    assert len(manager.status()["actions"]) >= 2


def test_manager_configure_swaps_assistant_and_keeps_history():
    manager = MeetingManager()
    manager.ingest("Hi there", "A")
    status = manager.configure(owner_name="Jay", name_variants=["Jay"])
    assert status["owner_name"] == "Jay"
    assert status["name_variants"] == ["Jay"]
    assert status["transcript_turns"] == 1


def test_manager_rejects_invalid_voice_sample(tmp_path):
    manager = MeetingManager()
    with pytest.raises(VoiceSampleProblem):
        manager.set_voice_sample(tmp_path / "missing.wav")


def test_manager_accepts_valid_voice_sample(tmp_path):
    sample = tmp_path / "sample.wav"
    _write_wav(sample, 30)
    manager = MeetingManager()
    result = manager.set_voice_sample(sample)
    assert result["saved"] is True
    assert manager.status()["voice"]["ready"] is True


def test_manager_stop_disables_assistant():
    manager = MeetingManager()
    manager.ingest("Hey owner?", "A")
    manager.ingest("owner please reply", "B")
    manager.configure(enabled=False)
    result = manager.ingest("owner, answer now", "A")
    assert result["kind"] == "IGNORED"


# -------------------------------------------------------------------- routes


def test_meeting_routes_require_pairing(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    with TestClient(app) as client:
        assert client.get("/meeting/status").status_code == 401
        assert client.post("/meeting/ingest", json={}).status_code == 401


def test_meeting_status_route(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    with TestClient(app) as client:
        response = client.get("/meeting/status", headers=_headers(client))
        assert response.status_code == 200
        assert response.json()["active"] is False


def test_meeting_ingest_and_transcript_route(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    with TestClient(app) as client:
        response = client.post(
            "/meeting/ingest",
            json={"text": "Hello team", "speaker": "A"},
            headers=_headers(client),
        )
        assert response.status_code == 200
        first = response.json()["kind"]

        response = client.post(
            "/meeting/ingest",
            json={"text": "owner, please answer this", "speaker": "B"},
            headers=_headers(client),
        )
        assert response.status_code == 200
        assert response.json()["kind"] in {"SPOKE", "IGNORED", "ERROR"}
        assert first == "IGNORED"

        response = client.get("/meeting/transcript", headers=_headers(client))
        assert response.status_code == 200
        texts = [row["text"] for row in response.json()["items"]]
        assert "Hello team" in texts


def test_meeting_transcript_never_returns_audio_or_paths(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    with TestClient(app) as client:
        client.post(
            "/meeting/ingest",
            json={"text": "owner, respond please", "speaker": "A"},
            headers=_headers(client),
        )
        body = client.get("/meeting/transcript", headers=_headers(client)).json()
        assert set(body["items"][0]) == {"speaker", "text"}


def test_meeting_stop_and_general_request_route(tmp_path):
    app = create_companion(tmp_path / "state", ["http://localhost:3000"])
    with TestClient(app) as client:
        # General participation is off by default -> ignored.
        response = client.post(
            "/meeting/say",
            json={"instruction": "Tell them the build is green"},
            headers=_headers(client),
        )
        assert response.json()["kind"] == "IGNORED"

        response = client.post("/meeting/stop", headers=_headers(client))
        assert response.status_code == 200
        assert client.get("/meeting/status", headers=_headers(client)).json()[
            "active"
        ] is False
