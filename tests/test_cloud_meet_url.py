import asyncio
from types import SimpleNamespace

import pytest

from app.meet_cloud.browser import CloudJoiner
from app.meet_agent.joiner import MeetJoiner
from app.meet_agent.link_detector import is_valid_meet_link


@pytest.mark.parametrize("path", ["/abc-defg-hij", "/meet/abc-defg-hij"])
@pytest.mark.parametrize("mode", ["guest", "google_profile"])
def test_cloud_url_uses_guest_route_once(monkeypatch, path, mode):
    seen = []

    async def join(self, url):
        assert is_valid_meet_link(url)
        seen.append(url)
        return "fixture"

    monkeypatch.setattr(MeetJoiner, "join", join)
    instance = CloudJoiner(
        audio=object(), settings=SimpleNamespace(meet_cloud_identity_mode=mode),
        on_turn=None, tick=None, leave_on_speech=False,
    )
    asyncio.run(instance.join(f"https://meet.google.com{path}?authuser=2&hs=122"))
    prefix = "/meet" if mode == "guest" else ""
    assert seen == [f"https://meet.google.com{prefix}/abc-defg-hij"]


@pytest.mark.parametrize("url", [
    "https://example.com/meet/abc-defg-hij",
    "https://meet.google.com/meet/meet/abc-defg-hij",
    "https://meet.google.com/meet/abc-defg-hij/extra",
    "https://user@meet.google.com/meet/abc-defg-hij",
])
def test_guest_route_validation_remains_strict(url):
    assert not is_valid_meet_link(url)
