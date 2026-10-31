import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_base_settings
from app.local_companion.server import Companion, create_companion
from app.main import app
from app.meet_agent.device import MeetDevice
from app.meet_agent.joiner import JoinEvidence, JoinResult, MeetJoiner
from app.meet_agent.link_detector import is_valid_meet_link

LINK = "https://meet.google.com/abc-defg-hij"
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


def event(**changes):
    return {
        "id": "meeting-1",
        "summary": "Review",
        "location": LINK,
        "start": {"dateTime": NOW.isoformat()},
        "end": {"dateTime": (NOW + timedelta(hours=1)).isoformat()},
        **changes,
    }


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("ENABLE_EXTERNAL_ACTIONS", "true")
    get_base_settings.cache_clear()
    seen = []

    def calendar(db, settings):
        seen.append(settings.dev_user_id)
        return SimpleNamespace(list_events=lambda **kw: [event()])

    monkeypatch.setattr("app.routes.meet.get_calendar_client", calendar)
    with TestClient(app) as browser:
        from app.db import SessionLocal
        from app.security.sessions import digest
        from app.services.runtime_state_store import RuntimeStateStore

        with SessionLocal() as db:
            RuntimeStateStore(db).delete("auth-attempts:" + digest("testclient"))
        credentials = {
            "email": str(uuid4()) + "@example.test",
            "password": "Fixture password 12345",
            "display_name": "Fixture",
        }
        response = browser.post(
            "/auth/register", json=credentials, headers={"Origin": "http://localhost:3000"}
        )
        assert response.status_code == 201, response.status_code
        user = response.json()["user"]
        token = browser.post("/meet/devices", headers={"Origin": "http://localhost:3000"}).json()[
            "token"
        ]
        yield browser, token, user, seen
    get_base_settings.cache_clear()


def test_device_credential_survives_logout_is_scoped_and_revocable(clients):
    browser, token, user, seen = clients
    browser.post("/auth/logout", headers={"Origin": "http://localhost:3000"})
    headers = {"Authorization": "Bearer " + token}
    assert browser.get("/meet/device/events").status_code == 401
    data = browser.get("/meet/device/events", headers=headers)
    assert data.status_code == 200
    assert data.json()["account_id"] == user["id"] and seen[-1] == user["id"]
    assert data.json()["items"][0]["url"] == LINK
    assert browser.get("/automations", headers=headers).status_code == 401
    assert browser.post("/meet/devices", headers=headers).status_code == 401
    assert browser.delete("/meet/device/authorization", headers=headers).status_code == 200
    assert browser.get("/meet/device/events", headers=headers).status_code == 401


def test_calendar_feed_rejects_cancelled_declined_ambiguous_and_all_day(clients, monkeypatch):
    browser, token, *_ = clients
    events = [
        event(status="cancelled"),
        event(attendees=[{"self": True, "responseStatus": "declined"}]),
        event(location=LINK + " https://meet.google.com/xyz-mnop-qrs"),
        event(start={"date": "2026-11-02"}),
        event(hangoutLink=LINK),
    ]
    monkeypatch.setattr(
        "app.routes.meet.get_calendar_client",
        lambda *args: SimpleNamespace(list_events=lambda **kw: events),
    )
    data = browser.get("/meet/device/events", headers={"Authorization": "Bearer " + token}).json()
    assert len(data["items"]) == 1


def test_attendance_is_persisted_once_and_restarts_do_not_rejoin(tmp_path):
    state = Companion(tmp_path)
    state.config["meet"] = {
        "enabled": True,
        "api_base": "https://fixture.test/api/jiraya",
        "device_token": "fixture",
        "account_id": "owner",
    }
    state.save()
    feed = {
        "status": "READY",
        "account_id": "owner",
        "items": [
            {
                "id": "event-1",
                "url": LINK,
                "start": NOW.isoformat(),
                "end": (NOW + timedelta(hours=1)).isoformat(),
            }
        ],
    }
    starts = []

    class Joiner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def join(self, url):
            starts.append((url, self.kwargs["profile_dir"]))
            self.kwargs["on_joined"]()
            return JoinResult(
                "JOINED", "IN_CALL_UI_VISIBLE", JoinEvidence(url, notes=["in_call_ui_visible"])
            )

    device = MeetDevice(state)
    device.feed = lambda config: feed
    device.poll(now=NOW, joiner_factory=Joiner)
    restored = MeetDevice(Companion(tmp_path))
    restored.feed = lambda config: feed
    restored.poll(now=NOW, joiner_factory=Joiner)
    assert len(starts) == 1
    assert restored.status()["recent"][0]["status"] == "COMPLETED"
    assert "device_token" not in str(restored.status())


@pytest.mark.parametrize("offset", [-121, 31, 3600])
def test_no_early_or_stale_join(tmp_path, offset):
    state = Companion(tmp_path)
    state.config["meet"] = {
        "enabled": True,
        "account_id": "owner",
        "api_base": "https://fixture.test",
        "device_token": "fixture",
    }
    device = MeetDevice(state)
    device.feed = lambda config: {
        "status": "READY",
        "account_id": "owner",
        "items": [
            {
                "id": "event",
                "url": LINK,
                "start": (NOW + timedelta(seconds=offset)).isoformat(),
                "end": (NOW + timedelta(hours=1)).isoformat(),
            }
        ],
    }
    device.poll(now=NOW, joiner_factory=lambda **kwargs: pytest.fail("Unexpected browser launch"))
    assert not state.config.get("meet_history")


def test_meet_routes_require_companion_token_and_return_real_app(tmp_path):
    with TestClient(create_companion(tmp_path, ["http://localhost:3000"])) as client:
        assert client.get("/meeting/status").status_code == 401
        headers = {"Authorization": "Bearer " + client.app.state.companion.config["token"]}
        assert client.get("/meeting/status", headers=headers).json()["enabled"] is False
        assert client.post("/meeting/pause", headers=headers).status_code == 200


def test_joiner_has_callable_wait_and_rejects_host_spoof_without_launch():
    joiner = MeetJoiner()
    assert callable(joiner._wait_in_call)
    for url in [
        "https://meet.google.com.evil.test/abc-defg-hij",
        "https://meet.google.com/abc-defg-hij/extra",
        "https://meet.google.com@evil.test/abc-defg-hij",
    ]:
        assert not is_valid_meet_link(url)
        assert asyncio.run(joiner.join(url)).reason == "INVALID_MEET_LINK"


def test_expired_device_credential_cannot_read_calendar(clients):
    from app.db import SessionLocal
    from app.security.sessions import digest
    from app.services.runtime_state_store import RuntimeStateStore

    browser, token, *_ = clients
    with SessionLocal() as db:
        store = RuntimeStateStore(db)
        key = "meet-device:" + digest(token)
        row = store.get(key)
        row["expires_at"] = "2020-01-01T00:00:00+00:00"
        store.set(key, row)
    assert (
        browser.get("/meet/device/events", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )


def test_external_action_gate_disables_automatic_feed(clients, monkeypatch):
    browser, token, *_ = clients
    monkeypatch.setenv("ENABLE_EXTERNAL_ACTIONS", "false")
    get_base_settings.cache_clear()
    response = browser.get("/meet/device/events", headers={"Authorization": "Bearer " + token})
    assert (
        response.json()["items"] == [] and response.json()["status"] == "EXTERNAL_ACTIONS_DISABLED"
    )


def test_retry_is_explicit_and_never_replays_completed_attendance(tmp_path):

    state = Companion(tmp_path)
    config = {
        "enabled": True,
        "account_id": "owner",
        "api_base": "https://fixture.test",
        "device_token": "fixture",
    }
    state.config["meet"] = config
    from app.meet_agent.device import owner_key

    owner = owner_key(config)
    key = "a" * 64
    state.config["meet_history"] = {key: {"key": key, "owner": owner, "status": "WAITING_FOR_AUTH"}}
    device = MeetDevice(state)
    assert device.retry(key)["recent"][0]["status"] == "RETRY_REQUESTED"
    state.config["meet_history"][key]["status"] = "COMPLETED"
    with pytest.raises(ValueError):
        device.retry(key)
    state.config["meet_history"][key]["status"] = "FAILED"
    state.config["meet_history"][key]["owner"] = "another-account"
    with pytest.raises(ValueError):
        device.retry(key)


def test_join_wait_respects_stop_without_missing_method_or_context_owner():
    import threading

    stop = threading.Event()
    stop.set()
    joiner = MeetJoiner(stop_event=stop)
    context = SimpleNamespace(pages=[object()])
    evidence = JoinEvidence(LINK)
    asyncio.run(joiner._wait_in_call(context, evidence, 60000))
    assert "requested_end_meeting" in evidence.notes


def test_media_off_verification_survives_changed_button_selector():
    class Page:
        off = False
        clicks = 0

        def locator(self, selector):
            page = self

            class Locator:
                @property
                def first(self):
                    return self

                async def is_visible(self):
                    return page.off if "turn on" in selector else not page.off

                async def click(self, **kwargs):
                    page.off = True
                    page.clicks += 1

            return Locator()

        async def wait_for_timeout(self, *args):
            pass

    page = Page()
    joiner = MeetJoiner()
    evidence = JoinEvidence(LINK)
    selectors = ('[aria-label*="microphone"][aria-label*="turn off"]',)
    assert asyncio.run(joiner._toggle_media_off(page, selectors, "mic_muted", evidence))
    assert asyncio.run(joiner._toggle_media_off(page, selectors, "mic_muted", evidence))
    assert page.clicks == 1


def test_changed_calendar_account_requires_device_reauthorization(tmp_path):
    state = Companion(tmp_path)
    state.config["meet"] = {
        "enabled": True,
        "account_id": "owner",
        "api_base": "https://fixture.test",
        "device_token": "fixture",
        "calendar_account": "old@example.test",
    }
    device = MeetDevice(state)
    device.feed = lambda config: {
        "status": "READY",
        "account_id": "owner",
        "calendar_account": "new@example.test",
        "items": [],
    }
    with pytest.raises(ValueError, match="Calendar account changed"):
        device.poll(now=NOW)


@pytest.mark.parametrize("outcome", ["JOINED", "FAILED", "KNOCKING", "MEDIA_BLOCKED"])
def test_joiner_distinguishes_admission_and_closes_resources(outcome):
    from app.meet_agent import selector_fallbacks as sf

    calls = []

    class Page:
        url = LINK

        async def goto(self, url, **kwargs):
            self.url = url

        async def screenshot(self, **kwargs):
            pass

        async def wait_for_selector(self, *args, **kwargs):
            pass

        async def wait_for_timeout(self, *args):
            await asyncio.sleep(0)

        def is_closed(self):
            return False

    class Context:
        def __init__(self):
            self.pages = [Page()]
            self.closed = False

        async def close(self):
            self.closed = True

    class Engine:
        stopped = False

        async def stop(self):
            self.stopped = True

    context, engine = Context(), Engine()

    class Joiner(MeetJoiner):
        async def _launch_context(self):
            return engine, context

        async def check_session(self, *args, **kwargs):
            return True

        async def _toggle_media_off(self, *args):
            return outcome != "MEDIA_BLOCKED"

        async def _click_first_visible(self, page, selectors):
            calls.append(selectors)
            return True, "fixture-button"

        async def _check_any_visible(self, page, selectors):
            return (
                (outcome == "JOINED" and selectors == sf.ADMITTED_MARKERS)
                or (outcome == "FAILED" and selectors == sf.ADMIT_ERROR_MARKERS)
                or (outcome == "KNOCKING" and selectors == sf.KNOCKING_MARKERS)
            )

    result = asyncio.run(Joiner(join_timeout_seconds=0.01, stay_seconds=0).join(LINK))
    assert result.status == ("FAILED" if outcome == "MEDIA_BLOCKED" else outcome)
    if outcome == "MEDIA_BLOCKED":
        assert sf.JOIN_BUTTON_SELECTORS not in calls
    if outcome == "JOINED":
        assert "in_call_ui_visible" in result.evidence.notes
    assert context.closed and engine.stopped
