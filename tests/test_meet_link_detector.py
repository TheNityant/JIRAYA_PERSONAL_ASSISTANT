"""Tests for meet_agent.link_detector — Brick 1 of the Meet agent layer."""

from app.meet_agent.link_detector import extract_meet_links, is_valid_meet_link


def test_valid_meet_link():
    assert is_valid_meet_link("https://meet.google.com/abc-defg-hij") is True


def test_link_with_digits_in_code_is_invalid():
    assert is_valid_meet_link("https://meet.google.com/abc-defg-hi1") is False


def test_non_meet_url_is_invalid():
    assert is_valid_meet_link("https://example.com/abc-defg-hij") is False
    assert is_valid_meet_link("") is False


def test_extract_from_email_body():
    body = (
        "Hi team,\n\nPlease join the review tomorrow:\n"
        "https://meet.google.com/abc-defg-hij.\n\n"
        "Backup room: https://meet.google.com/xyz-mnop-qrs\n"
    )
    assert extract_meet_links(body) == [
        "https://meet.google.com/abc-defg-hij",
        "https://meet.google.com/xyz-mnop-qrs",
    ]


def test_extract_deduplicates_and_ignores_empty():
    body = "See https://meet.google.com/abc-defg-hij and again https://meet.google.com/abc-defg-hij"
    assert extract_meet_links(body) == ["https://meet.google.com/abc-defg-hij"]
    assert extract_meet_links("") == []
    assert extract_meet_links("no links here") == []
