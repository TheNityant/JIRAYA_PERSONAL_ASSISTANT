import asyncio
from types import SimpleNamespace

from app.meet_agent.joiner import MeetJoiner
from app.meet_cloud.browser import CloudJoiner
from app.meet_cloud.profiles import account_matches, profile_key, profile_path


def test_profiles_are_bound_to_both_user_and_calendar_without_path_injection():
    assert profile_key("a", "user@example.com") == profile_key("a", "USER@example.com")
    assert profile_key("a", "user@example.com") != profile_key("b", "user@example.com")
    assert profile_key("a", "user@example.com") != profile_key("a", "other@example.com")
    path = profile_path("/profiles", "../../escape", "../email")
    assert str(path.parent) == "/profiles" and len(path.name) == 64


def page(labels, url="https://meet.google.com/"):
    class Controls:
        async def count(self):
            return len(labels)

        def nth(self, i):
            async def attribute(name):
                return labels[i]

            return SimpleNamespace(get_attribute=attribute)

    return SimpleNamespace(url=url, locator=lambda selector: Controls())


def test_account_matching_needs_exact_email_on_meet_not_a_login_page():
    assert asyncio.run(
        account_matches(page(["Google Account: Nityant (user@example.com)"]), "user@example.com")
    )
    assert not asyncio.run(
        account_matches(page(["Google Account: Other (other@example.com)"]), "user@example.com")
    )
    assert not asyncio.run(account_matches(page([]), "user@example.com"))
    assert not asyncio.run(
        account_matches(
            page(["Google Account: user@example.com"], "https://accounts.google.com/signin"),
            "user@example.com",
        )
    )
    assert not asyncio.run(
        account_matches(page(["Google Account: notuser@example.com"]), "user@example.com")
    )


def test_profile_mode_never_falls_back_to_guest(monkeypatch):
    async def signed_in(self, *args, **kwargs):
        return True

    monkeypatch.setattr(MeetJoiner, "check_session", signed_in)
    joiner = CloudJoiner(
        audio=object(),
        settings=SimpleNamespace(meet_cloud_identity_mode="google_profile"),
        expected_account="user@example.com",
        on_turn=None,
        tick=None,
        leave_on_speech=False,
    )
    assert not asyncio.run(joiner.check_session(page(["Google Account: other@example.com"])))
    assert asyncio.run(joiner.check_session(page(["Google Account: user@example.com"])))


def test_calendar_browser_account_index_is_not_forwarded_to_aws(monkeypatch):
    seen = []

    async def join(self, url):
        seen.append(url)
        return "fixture"

    monkeypatch.setattr(MeetJoiner, "join", join)
    instance = CloudJoiner(
        audio=object(), settings=object(), on_turn=None, tick=None, leave_on_speech=False
    )
    assert (
        asyncio.run(instance.join("https://meet.google.com/abc-defg-hij?authuser=2&hs=122"))
        == "fixture"
    )
    assert seen == ["https://meet.google.com/abc-defg-hij"]
