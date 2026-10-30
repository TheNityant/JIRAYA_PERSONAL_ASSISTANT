from types import SimpleNamespace

import pytest

from app.config import Settings
from app.providers.nebius import NebiusProvider


def provider(monkeypatch, finish="stop", content='{}'):
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])
    monkeypatch.setattr("app.providers.nebius.OpenAI", lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return NebiusProvider(Settings(nebius_api_key="fixture-only")), calls


def test_event_extraction_has_larger_bounded_output_without_changing_normal_calls(monkeypatch):
    client, calls = provider(monkeypatch)
    assert client.complete_json("Extract one event") == '{}'
    assert calls[-1]["max_tokens"] == 1024
    assert calls[-1]["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    client.complete("Match event", enable_thinking=False)
    assert calls[-1]["max_tokens"] == 256


def test_truncated_model_response_never_passes_as_json(monkeypatch):
    client, _ = provider(monkeypatch, finish="length", content='{"start":')
    with pytest.raises(RuntimeError, match="output limit"):
        client.complete_json("Extract event")


@pytest.mark.parametrize("start,zone", [
    ("2026-11-01T01:30:00", "America/New_York"),
    ("2026-03-08T02:30:00", "America/New_York"),
    ("2026-10-30", "Asia/Kolkata"),
])
def test_ambiguous_nonexistent_or_date_only_times_are_not_booked(start, zone):
    from app.automation.calendar_extraction import EventDetailsRequired, apply_event_defaults
    with pytest.raises(EventDetailsRequired):
        apply_event_defaults({"summary": "Meeting", "start": start, "end": None}, zone=zone, duration=30)


def test_unconfigured_duration_defaults_to_one_hour_but_explicit_range_wins():
    from app.automation.calendar_extraction import apply_event_defaults, event_defaults
    rule = SimpleNamespace(action_config={"event_time_zone": "Asia/Kolkata"}, context_config={})
    zone, duration = event_defaults(rule)
    assert duration == 60
    result = apply_event_defaults({"summary": "Meeting", "start": "2026-10-30T15:00:00", "end": None}, zone=zone, duration=duration)
    assert result["end"].isoformat() == "2026-10-30T16:00:00+05:30"
    result = apply_event_defaults({"summary": "Meeting", "start": "2026-10-30T15:00:00", "end": "2026-10-30T17:00:00"}, zone=zone, duration=duration)
    assert result["end"].isoformat() == "2026-10-30T17:00:00+05:30"
    rule.action_config["event_duration_minutes"] = 45
    assert event_defaults(rule)[1] == 45
