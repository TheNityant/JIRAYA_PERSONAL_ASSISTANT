"""Keep exact source links even when the extraction model omits them."""

import base64
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_calendar_integration import Calendar, Provider, automation

from app.automation.calendar_links import calendar_link_details
from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.integrations.gmail.client import GmailClient
from app.integrations.gmail.content import source_links
from app.models import AutomationExecution

LINK = "https://meet.google.com/meet/abc-defg-hij"


@pytest.fixture
def database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def email(body):
    return SimpleNamespace(
        id="linked-email",
        thread_id="linked-thread",
        sender="friend@example.test",
        subject="Hackathon meeting update",
        body=body,
        internal_date_ms=1793362200000,
    )


@pytest.mark.parametrize("policy", ["AUTO", "REQUIRED", "DRAFT_ONLY"])
def test_model_omission_cannot_drop_source_meeting_link(database, monkeypatch, policy):
    calendar = Calendar()
    monkeypatch.setattr("app.automation.calendar_action.get_calendar_client", lambda *a: calendar)
    message = email(f"Meet on 30 October at 9 PM. Here is the meeting link: {LINK}")
    executor = AutomationExecutor(
        settings=Settings(_env_file=None, enable_external_actions=True),
        db=database,
        provider=Provider(),
        gmail=SimpleNamespace(get_message=lambda _: message),
    )
    rule = automation(database, policy)
    result = executor.run(automation=rule, message_id=message.id)
    proposal = result["execution"]["output"]["proposal"]
    assert proposal["location"] == LINK
    assert LINK in proposal["description"]
    assert result["execution"]["output"]["source_links"] == [LINK]
    if policy == "REQUIRED":
        execution = database.get(AutomationExecution, result["execution"]["id"])
        result = executor.approve(execution=execution)
    if policy == "DRAFT_ONLY":
        assert calendar.inserts == 0
    else:
        assert result["verification_status"] == "VERIFIED_SUCCESS"
        event = next(iter(calendar.events.values()))
        assert event["location"] == LINK
        assert LINK in event["description"]
        assert "conferenceData" not in event  # Preserve the supplied URL; do not generate another.
        assert (
            executor.run(automation=rule, message_id=message.id)["decision"] == "ALREADY_EXECUTED"
        )
        assert calendar.inserts == 1


def test_provider_readback_must_contain_the_preserved_link(database, monkeypatch):
    calendar = Calendar()
    original = calendar.get_event
    calendar.get_event = lambda **kwargs: {
        **original(**kwargs),
        "description": "Dropped",
        "location": "",
    }
    monkeypatch.setattr("app.automation.calendar_action.get_calendar_client", lambda *a: calendar)
    message = email(f"Meeting link: {LINK}")
    executor = AutomationExecutor(
        settings=Settings(_env_file=None, enable_external_actions=True),
        db=database,
        provider=Provider(),
        gmail=SimpleNamespace(get_message=lambda _: message),
    )
    result = executor.run(automation=automation(database, "AUTO"), message_id=message.id)
    assert result["verification_status"] == "UNOBSERVED"
    assert result["execution"]["output"]["reason_code"] == "EVENT_LINKS_NOT_VERIFIED"


def test_html_only_email_keeps_href_target_and_excludes_old_quoted_links():
    html = f'''<div>Meet at 9 PM.</div><div><a href="{LINK}?authuser=0&amp;item=2">Join meeting</a></div>
    <script>https://meet.google.com/script-content</script>
    <blockquote><a href="https://meet.google.com/old-old-old">Old meeting</a></blockquote>'''
    payload = {
        "mimeType": "text/html",
        "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
    }
    text = (
        GmailClient.__new__(GmailClient)
        ._parse_message({"id": "html-email", "payload": payload})
        .body
    )
    assert "Join meeting" in text
    assert source_links(text) == [LINK + "?authuser=0&item=2"]
    assert "script-content" not in text
    assert "javascript:" not in GmailClient._html_to_text('<a href="javascript:alert(1)">Join</a>')


def test_multiple_links_are_deduplicated_without_choosing_a_meeting_arbitrarily():
    second = "https://company.zoom.us/j/123?pwd=provided-passcode"
    details, links = calendar_link_details(
        "Summary", f"Meet: <{LINK}>, or ({second}). Again {LINK}"
    )
    assert links == [LINK, second]
    assert details["location"] == ""
    assert all(url in details["description"] for url in links)
    assert source_links(
        "https://user:password@example.test/private https://meet.google.com.evil.test/x"
    ) == ["https://meet.google.com.evil.test/x"]
    details, _ = calendar_link_details("Summary", "https://meet.google.com.evil.test/x")
    assert details["location"] == ""


def test_long_summary_cannot_truncate_the_source_url():
    details, links = calendar_link_details("x" * 8000, f"Meeting: {LINK}")
    assert len(details["description"]) <= 8000
    assert LINK in details["description"] and links == [LINK]


def test_html_alternative_adds_button_target_missing_from_plain_text_before_quote_boundary():
    def part(kind, text):
        return {
            "mimeType": kind,
            "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()},
        }

    plain = "Meet tomorrow. Join meeting.\n\nOn Monday, Friend wrote:\n> Old message"
    html = f'<div>Meet tomorrow. <a href="{LINK}">Join meeting</a></div><blockquote><a href="https://meet.google.com/old-old-old">Old</a></blockquote>'
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [part("text/plain", plain), part("text/html", html)],
    }
    body = GmailClient._extract_body(payload)
    assert source_links(body) == [LINK]
    assert "Old message" in body  # Raw quoted evidence remains available.
