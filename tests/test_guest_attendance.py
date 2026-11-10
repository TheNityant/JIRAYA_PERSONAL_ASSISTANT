import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.meet_agent.joiner import MeetJoiner
from app.meet_cloud.browser import CloudJoiner
from app.meet_cloud.guest import effective_settings, guest_name


def test_default_guest_deployment_overrides_previous_profile_environment():
    settings = Settings(
        meet_cloud_identity_mode="google_profile", meet_cloud_browser_portal_enabled=True
    )
    effective = effective_settings(settings)
    assert effective.meet_cloud_identity_mode == "guest"
    assert not effective.meet_cloud_browser_portal_enabled
    assert settings.meet_cloud_identity_mode == "google_profile"


def test_guest_label_prefers_profile_then_calendar_then_email():
    assert (
        guest_name(SimpleNamespace(display_name=" Nityant   Tiwari ", email="a@example.test"))
        == "Nityant Tiwari"
    )
    assert (
        guest_name(
            SimpleNamespace(display_name="Local user", email="a@example.test"),
            SimpleNamespace(display_name="Calendar User"),
        )
        == "Calendar User"
    )
    assert guest_name(SimpleNamespace(display_name="", email="nityant@example.test")) == "nityant"
    assert guest_name(None) == "Jiraya"


def test_guest_browser_fills_user_name_without_google_signin(monkeypatch):
    seen = []

    class Field:
        @property
        def first(self):
            return self

        async def is_visible(self):
            return True

        async def fill(self, value):
            seen.append(value)

    async def media(self, *args):
        return True

    monkeypatch.setattr(MeetJoiner, "_toggle_media_off", media)
    joiner = CloudJoiner(
        audio=object(),
        settings=SimpleNamespace(meet_cloud_identity_mode="guest"),
        display_name="Nityant Tiwari",
        on_turn=None,
        tick=None,
        leave_on_speech=False,
    )
    assert asyncio.run(joiner.check_session(object()))
    assert asyncio.run(
        joiner._toggle_media_off(
            SimpleNamespace(locator=lambda s: Field()), (), "mic_muted", object()
        )
    )
    assert seen == ["Nityant Tiwari"]


def test_api_requires_worker_update_when_old_worker_still_reports_google_profile():
    from datetime import UTC, datetime

    from app.db import SessionLocal, init_db
    from app.meet_cloud.identity import identity_mode
    from app.routes.meet_cloud import availability
    from app.services.runtime_state_store import RuntimeStateStore

    init_db()
    with SessionLocal() as db:
        store = RuntimeStateStore(db)
        previous = store.get("cloud-meet-worker")
        try:
            store.set(
                "cloud-meet-worker",
                {
                    "at": datetime.now(UTC).isoformat(),
                    "identity_mode": "google_profile",
                    "browser_portal": True,
                },
            )
            settings = Settings(meet_cloud_guest_only=True)
            assert identity_mode(db, settings) == "guest"
            status = availability(db, settings)
            assert status["worker_update_required"] and not status["worker_online"]
            assert not status["browser_portal_available"]
        finally:
            if previous:
                store.set("cloud-meet-worker", previous)
            else:
                store.delete("cloud-meet-worker")
