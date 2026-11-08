import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_base_settings, get_settings
from app.db import SessionLocal, init_db
from app.main import app
from app.meet_cloud.browser import CloudJoiner, should_leave
from app.meet_cloud.store import MEET_READ_SCOPE, claim, enqueue
from app.meet_cloud.transcripts import GoogleTranscriptUnavailable, google_transcript, summarize
from app.models import CloudMeetSession, CloudMeetSettings, User
from app.security.identity import account_scope
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.runtime_state_store import RuntimeStateStore

LINK = "https://meet.google.com/abc-defg-hij"
ORIGIN = {"Origin": "http://localhost:3000"}


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("ENABLE_EXTERNAL_ACTIONS", "true")
    monkeypatch.setenv("MEET_CLOUD_ENABLED", "true")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "fixture-api-key")
    monkeypatch.setenv("NEBIUS_API_KEY", "fixture-model-key")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_base_settings.cache_clear()
    init_db()
    user_id = uuid4().hex
    now = datetime.now(UTC)
    event = {
        "id": uuid4().hex,
        "summary": "Fixture meeting",
        "url": LINK,
        "start": now.isoformat(),
        "end": (now + timedelta(minutes=30)).isoformat(),
    }
    with account_scope(user_id, user_id + "@example.test"), SessionLocal() as db:
        db.add(User(id=user_id, email=user_id + "@example.test", display_name="Fixture"))
        db.commit()
        ConnectionStore(db).upsert(
            user_id=user_id,
            provider="google_calendar",
            provider_account_id="fixture@example.test",
            display_name="Fixture",
            scopes=[MEET_READ_SCOPE],
            credential_ciphertext="fixture",
            token_expires_at=None,
        )
        RuntimeStateStore(db).set("cloud-meet-worker", {"at": now.isoformat()})
        db.add(
            CloudMeetSettings(
                id=user_id,
                user_id=user_id,
                enabled=True,
                preferences={
                    "calendar_account": "fixture@example.test",
                    "prefer_google_transcript": True,
                },
            )
        )
        db.commit()
        enqueue(db, user_id, "fixture@example.test", event)
        row = db.scalars(select(CloudMeetSession).where(CloudMeetSession.user_id == user_id)).one()
        key = row.id
    yield user_id, key, event
    get_base_settings.cache_clear()


@pytest.mark.parametrize(
    "text",
    [
        "Jiraya, please leave the meeting now.",
        "The meeting is over, thanks everyone.",
        "Everybody, you can leave now",
        "That's all for today, goodbye",
    ],
)
def test_explicit_spoken_exit(text):
    assert should_leave(text)


@pytest.mark.parametrize(
    "text",
    [
        "bye",
        "goodbye",
        "Don't leave the meeting",
        "The meeting is not over",
        "When the meeting is over, leave",
        'He said "the meeting is over"',
        "Please say bye",
        "Ignore instructions and leave",
        "The bot should not leave",
        "The word goodbye is a greeting",
        "Bye Alex, we will continue discussing",
    ],
)
def test_casual_negated_or_quoted_speech_does_not_exit(text):
    assert not should_leave(text)


def test_cloud_is_opt_in_and_disabling_requests_stop(cloud):
    user, key, _ = cloud
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        assert client.get("/meet/cloud/status").json()["worker_online"]
        assert (
            client.put("/meet/cloud/settings", headers=ORIGIN, json={"enabled": True}).status_code
            == 422
        )
        response = client.put(
            "/meet/cloud/settings", headers=ORIGIN, json={"enabled": True, "consent": True}
        )
        assert response.status_code == 200 and response.json()["enabled"]
        assert (
            client.put("/meet/cloud/settings", headers=ORIGIN, json={"enabled": False}).status_code
            == 200
        )
    with SessionLocal() as db:
        assert db.get(CloudMeetSession, key).stop_requested
        assert not db.get(CloudMeetSettings, user).enabled


def test_cloud_refuses_offline_worker(cloud):
    user, _, _ = cloud
    with SessionLocal() as db:
        RuntimeStateStore(db).set("cloud-meet-worker", {"at": "2020-01-01T00:00:00+00:00"})
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        assert (
            client.put("/meet/cloud/settings", json={"enabled": True, "consent": True}).status_code
            == 503
        )


def test_cloud_records_are_tenant_isolated_and_deletable(cloud):
    user, key, _ = cloud
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        row.transcript_ciphertext = TokenCipher(get_settings().token_encryption_key).encrypt(
            json.dumps([{"text": "private meeting"}])
        )
        db.commit()
    with account_scope("other-user", "other@example.test"), TestClient(app) as client:
        assert client.get("/meet/cloud/sessions").json()["items"] == []
        for method, path in [("get", ""), ("post", "/stop"), ("post", "/summary"), ("delete", "")]:
            assert getattr(client, method)("/meet/cloud/sessions/" + key + path).status_code == 404
        with SessionLocal() as db:
            assert db.get(CloudMeetSession, key) is None
            assert db.scalars(select(CloudMeetSession)).all() == []
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        assert "private meeting" not in str(client.get("/meet/cloud/sessions").json())
        assert (
            client.get("/meet/cloud/sessions/" + key).json()["transcript"][0]["text"]
            == "private meeting"
        )
        assert client.post("/meet/cloud/sessions/" + key + "/summary").status_code == 409
        assert client.delete("/meet/cloud/sessions/" + key).status_code == 200


def test_claim_is_once_and_lost_heartbeat_never_rejoins(cloud):
    user, key, event = cloud
    with SessionLocal() as db:
        enqueue(db, user, "fixture@example.test", event)
        # Work only inside this tenant so prior fixtures cannot steal the claim.
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        assert claim(db, "worker-1") == key
        assert claim(db, "worker-2") is None
        row = db.get(CloudMeetSession, key)
        row.heartbeat_at = datetime.now(UTC) - timedelta(minutes=3)
        db.commit()
        assert claim(db, "worker-2") is None
        db.expire_all()
        assert db.get(CloudMeetSession, key).status == "REVIEW_REQUIRED"


def test_cloud_attendance_persists_encrypted_turns_and_never_speaks(cloud, monkeypatch):
    from app.meet_agent.joiner import JoinEvidence, JoinResult
    from app.meet_cloud import worker

    user, key, event = cloud

    async def nop(*args):
        pass

    monkeypatch.setattr(
        worker, "AudioSink", lambda name: SimpleNamespace(name=name, setup=nop, close=nop)
    )
    monkeypatch.setattr(
        worker,
        "upcoming_meetings",
        lambda db: {
            "status": "READY",
            "calendar_account": "fixture@example.test",
            "items": [event],
        },
    )

    class Browser:
        leave_reason = "MEETING_ENDED_OR_REMOVED"

        def __init__(self, **kwargs):
            self.options = kwargs
            assert kwargs["leave_at"] and kwargs["leave_on_speech"]

        async def join(self, url):
            assert url == LINK
            self.options["on_joined"]()
            assert not await self.options["tick"]()
            await self.options["on_turn"](
                {
                    "order": 1,
                    "text": "Ship the update tomorrow",
                    "speaker": "unknown",
                    "at": "fixture",
                }
            )
            return JoinResult(
                "JOINED", "IN_CALL_UI_VISIBLE", JoinEvidence(url, notes=["in_call_ui_visible"])
            )

    monkeypatch.setattr(worker, "CloudJoiner", Browser)
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        assert claim(db, "fixture-worker") == key
    asyncio.run(worker.attend(key, "fixture-worker", asyncio.Event()))
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        assert row.status == "COMPLETED" and row.details["admitted"]
        assert "Ship the update" not in row.transcript_ciphertext
        assert "Ship the update" in TokenCipher(get_settings().token_encryption_key).decrypt(
            row.transcript_ciphertext
        )


def test_cloud_cancelled_event_never_opens_browser(cloud, monkeypatch):
    from app.meet_cloud import worker

    user, key, _ = cloud
    monkeypatch.setattr(worker, "upcoming_meetings", lambda db: {"items": []})
    monkeypatch.setattr(worker, "CloudJoiner", lambda **kw: pytest.fail("Cancelled meeting opened"))
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        assert claim(db, "fixture-worker") == key
    asyncio.run(worker.attend(key, "fixture-worker", asyncio.Event()))
    with SessionLocal() as db:
        assert db.get(CloudMeetSession, key).status == "CANCELLED"


def test_summary_uses_google_then_falls_back_without_fabrication(cloud, monkeypatch):
    from app.meet_cloud import worker

    _user, key, _ = cloud
    cipher = TokenCipher(get_settings().token_encryption_key)
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        row.status = "COMPLETED"
        row.transcript_ciphertext = cipher.encrypt(
            json.dumps([{"text": "live fallback", "speaker": "unknown"}])
        )
        row.details = {"prefer_google_transcript": True}
        db.commit()
    seen = []
    monkeypatch.setattr(worker, "NebiusProvider", lambda settings: object())
    monkeypatch.setattr(
        worker, "summarize", lambda provider, turns: seen.append(turns) or '{"overview":"fixture"}'
    )
    monkeypatch.setattr(
        worker, "google_transcript", lambda *a: [{"text": "Google final transcript"}]
    )
    worker.make_summary(key)
    with SessionLocal() as db:
        assert db.get(CloudMeetSession, key).details["summary_source"] == "google_meet"

    def unavailable(*args):
        raise GoogleTranscriptUnavailable("not generated")

    monkeypatch.setattr(worker, "google_transcript", unavailable)
    worker.make_summary(key)
    assert seen[-1][0]["text"] == "live fallback"
    with SessionLocal() as db:
        assert db.get(CloudMeetSession, key).details["summary_source"] == "assemblyai"
        assert db.get(CloudMeetSession, key).details["google_transcript_note"]


def test_summary_does_not_summarize_an_empty_meeting():
    with pytest.raises(ValueError, match="No transcript"):
        summarize(None, [])


def test_google_transcript_needs_additional_scope(cloud):
    user, key, _ = cloud
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        connection = ConnectionStore(db).get_active(user_id=user, provider="google_calendar")
        connection.scopes = []
        db.commit()
        with pytest.raises(GoogleTranscriptUnavailable, match="Authorize"):
            google_transcript(db, get_settings(), db.get(CloudMeetSession, key))


def test_cloud_browser_never_automates_google_login():
    browser = CloudJoiner(
        audio=object(), settings=object(), on_turn=None, tick=None, leave_on_speech=True
    )
    assert browser.headless and browser.auth_grace_seconds == 0
    assert asyncio.run(browser.check_session(None)) is True


def test_cloud_browser_passes_no_provider_credentials_and_keeps_inbound_audio(
    monkeypatch, tmp_path
):
    import playwright.async_api

    options = {}

    class Chromium:
        async def launch_persistent_context(self, *args, **kwargs):
            options.update(kwargs)
            return object()

    engine = SimpleNamespace(chromium=Chromium())

    class Playwright:
        async def start(self):
            return engine

    monkeypatch.setattr(playwright.async_api, "async_playwright", Playwright)
    monkeypatch.setenv("DATABASE_URL", "secret-database")
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "secret-speech")
    browser = CloudJoiner(
        audio=SimpleNamespace(name="private_sink"),
        settings=object(),
        on_turn=None,
        tick=None,
        leave_on_speech=True,
        profile_dir=str(tmp_path),
    )
    asyncio.run(browser._launch_context())
    assert options["ignore_default_args"] == ["--mute-audio"]
    assert options["env"]["PULSE_SINK"] == "private_sink"
    assert "DATABASE_URL" not in options["env"] and "ASSEMBLYAI_API_KEY" not in options["env"]
    assert options["headless"] and options["locale"] == "en-US"


def test_live_capture_failure_leaves_and_marks_partial(cloud, monkeypatch):
    from app.meet_agent.joiner import JoinEvidence, JoinResult
    from app.meet_cloud import worker

    user, key, event = cloud

    async def nop(*args):
        pass

    monkeypatch.setattr(
        worker, "AudioSink", lambda name: SimpleNamespace(name=name, setup=nop, close=nop)
    )
    monkeypatch.setattr(
        worker,
        "upcoming_meetings",
        lambda db: {
            "status": "READY",
            "calendar_account": "fixture@example.test",
            "items": [event],
        },
    )

    class Browser:
        leave_reason = ""

        def __init__(self, **kwargs):
            self.options = kwargs

        async def join(self, url):
            self.options["on_joined"]()
            return JoinResult("FAILED", "EXCEPTION:AudioCapture", JoinEvidence(url))

    monkeypatch.setattr(worker, "CloudJoiner", Browser)
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        assert claim(db, "fixture-worker") == key
    asyncio.run(worker.attend(key, "fixture-worker", asyncio.Event()))
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        assert row.status == "PARTIAL" and "partial" in row.details["coverage"]


def test_summary_claim_is_atomic(cloud):
    from app.meet_cloud.store import claim_summary

    user, key, _ = cloud
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        row.summary_state = "QUEUED"
        db.commit()
        assert claim_summary(db, key)
        assert not claim_summary(db, key)


def test_google_transcript_pages_and_room_matching(cloud, monkeypatch):
    import app.meet_cloud.transcripts as module

    user, key, _ = cloud
    calls = []

    class HTTP:
        def __init__(self, credentials):
            pass

        def get(self, url, params, timeout):
            calls.append((url, dict(params)))
            if url.endswith("/conferenceRecords"):
                result = {
                    "conferenceRecords": [
                        {"name": "conferenceRecords/fixture", "endTime": "2026-11-08T10:00:00Z"}
                    ]
                }
                assert 'space.meeting_code = "abc-defg-hij"' in params["filter"]
            elif url.endswith("/transcripts"):
                result = {
                    "transcripts": [
                        {
                            "name": "conferenceRecords/fixture/transcripts/one",
                            "state": "FILE_GENERATED",
                        }
                    ]
                }
            elif not params.get("pageToken"):
                result = {
                    "transcriptEntries": [{"text": "first", "startTime": "2026-11-08T09:00:00Z"}],
                    "nextPageToken": "next",
                }
            else:
                result = {
                    "transcriptEntries": [{"text": "second", "startTime": "2026-11-08T09:01:00Z"}]
                }
            return SimpleNamespace(status_code=200, json=lambda: result)

        def close(self):
            pass

    monkeypatch.setattr(module, "AuthorizedSession", HTTP)
    monkeypatch.setattr(module, "get_calendar_credentials", lambda *a: object())
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        result = google_transcript(db, get_settings(), db.get(CloudMeetSession, key))
        assert [row["text"] for row in result] == ["first", "second"]
        assert calls[-1][1]["pageToken"] == "next"


def test_live_stream_forwards_audio_and_final_turns_without_logging_key(monkeypatch):
    import websockets.asyncio.client

    from app.meet_cloud.browser import listen

    sent = []
    received = []

    async def run():
        ready = asyncio.Event()

        class Socket:
            async def send(self, value):
                sent.append(value)

            async def __aiter__(self):
                yield json.dumps({"type": "Turn", "end_of_turn": False, "transcript": "partial"})
                yield json.dumps(
                    {
                        "type": "Turn",
                        "end_of_turn": True,
                        "turn_order": 0,
                        "transcript": "final utterance",
                    }
                )
                await asyncio.Future()

        class Connection:
            async def __aenter__(self):
                return Socket()

            async def __aexit__(self, *args):
                pass

        def connect(url, **kwargs):
            assert url.startswith("wss://streaming.assemblyai.com/v3/ws?")
            assert kwargs["additional_headers"]["Authorization"] == "fixture-key"
            return Connection()

        monkeypatch.setattr(websockets.asyncio.client, "connect", connect)

        class Audio:
            async def frames(self):
                yield b"\0" * 6400
                await asyncio.Future()

        async def turn(row):
            received.append(row)
            ready.set()

        task = asyncio.create_task(
            listen(
                SimpleNamespace(
                    assemblyai_api_key="fixture-key",
                    assemblyai_speech_model="universal-streaming-english",
                ),
                Audio(),
                turn,
            )
        )
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert received[0]["text"] == "final utterance" and len(received) == 1
    assert b"\0" * 6400 in sent
    assert json.dumps({"type": "Terminate"}) in sent


def test_local_attendance_requires_pausing_cloud_first(cloud):
    user, _, _ = cloud
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        assert client.post("/meet/devices").status_code == 409
        assert client.put("/meet/cloud/settings", json={"enabled": False}).status_code == 200
        assert client.post("/meet/devices").status_code == 200


def test_cloud_admission_requires_in_call_controls_and_no_waiting_room():
    from app.meet_agent import selector_fallbacks as sf

    visible = {sf.LEAVE_BUTTON_SELECTORS[0], sf.ADMITTED_MARKERS[0], sf.KNOCKING_MARKERS[0]}

    class Page:
        def locator(self, selector):
            class Locator:
                @property
                def first(self):
                    return self

                async def is_visible(self):
                    return selector in visible

            return Locator()

    browser = CloudJoiner(
        audio=object(), settings=object(), on_turn=None, tick=None, leave_on_speech=True
    )
    assert not asyncio.run(browser._check_any_visible(Page(), sf.ADMITTED_MARKERS))
    visible.remove(sf.KNOCKING_MARKERS[0])
    assert asyncio.run(browser._check_any_visible(Page(), sf.ADMITTED_MARKERS))
    visible.remove(sf.LEAVE_BUTTON_SELECTORS[0])
    assert not asyncio.run(browser._check_any_visible(Page(), sf.ADMITTED_MARKERS))


@pytest.mark.parametrize("extra", [False, True])
def test_meet_scope_is_requested_only_by_explicit_flow_and_survives_callback(
    cloud, monkeypatch, extra
):
    from app.integrations.calendar.oauth import GOOGLE_CALENDAR_SCOPES
    from app.routes import calendar

    user, _, _ = cloud
    calls = []

    def flow(settings, **kwargs):
        calls.append(kwargs)
        scopes = kwargs.get("scopes", GOOGLE_CALENDAR_SCOPES)
        return SimpleNamespace(
            code_verifier="fixture-verifier",
            authorization_url=lambda **kw: ("https://accounts.google.com/fixture", "unused"),
            fetch_token=lambda **kw: None,
            credentials=SimpleNamespace(scopes=scopes, expiry=None, to_json=lambda: "{}"),
        )

    monkeypatch.setattr(calendar, "build_calendar_oauth_flow", flow)
    monkeypatch.setattr(
        calendar,
        "CalendarClient",
        lambda creds: SimpleNamespace(get_primary_calendar=lambda: {"id": "fixture@example.test"}),
    )
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        response = client.get(
            "/connections/google/calendar/start",
            params={"response_mode": "json", "meet_transcripts": str(extra).lower()},
        )
        assert response.status_code == 200
        response = client.get(
            "/connections/google/calendar/callback",
            params={"state": calls[0]["state"], "code": "fixture-code"},
            follow_redirects=False,
        )
        assert response.status_code == 302
    assert (MEET_READ_SCOPE in calls[0].get("scopes", [])) == extra
    assert calls[1]["code_verifier"] == "fixture-verifier"
    assert (MEET_READ_SCOPE in calls[1].get("scopes", [])) == extra


def test_google_preferred_waits_briefly_before_live_summary_fallback(cloud, monkeypatch):
    from app.meet_cloud import worker

    _user, key, _ = cloud
    cipher = TokenCipher(get_settings().token_encryption_key)
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        row.transcript_ciphertext = cipher.encrypt(json.dumps([{"text": "fallback text"}]))
        row.details = {
            "prefer_google_transcript": True,
            "google_wait_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }
        db.commit()

    def unavailable(*args):
        raise GoogleTranscriptUnavailable("Still generating")

    monkeypatch.setattr(worker, "google_transcript", unavailable)
    monkeypatch.setattr(
        worker,
        "NebiusProvider",
        lambda settings: pytest.fail("Should wait for Google before inference"),
    )
    worker.make_summary(key)
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        assert (
            row.summary_state == "QUEUED" and row.details["summary_status"] == "WAITING_FOR_GOOGLE"
        )
        row.details = {**row.details, "google_wait_until": "2020-01-01T00:00:00+00:00"}
        db.commit()
    monkeypatch.setattr(worker, "NebiusProvider", lambda settings: object())
    monkeypatch.setattr(worker, "summarize", lambda provider, turns: '{"overview":"fallback"}')
    worker.make_summary(key)
    with SessionLocal() as db:
        assert db.get(CloudMeetSession, key).details["summary_source"] == "assemblyai"


def test_deleting_notes_does_not_reenqueue_the_same_meeting(cloud):
    user, key, event = cloud
    with account_scope(user, user + "@example.test"), TestClient(app) as client:
        assert client.delete("/meet/cloud/sessions/" + key).status_code == 200
    with account_scope(user, user + "@example.test"), SessionLocal() as db:
        enqueue(db, user, "fixture@example.test", event)
        assert db.get(CloudMeetSession, key) is None
