import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_generic_automation_execution import FakeGmail, make_automation

from app.automation import scheduling_reply as scheduling
from app.automation.executor import AutomationExecutor
from app.config import Settings
from app.db import Base
from app.models import AutomationExecution

NOW = datetime(2026, 10, 26, 3, 30, tzinfo=UTC)  # Monday 09:00 Asia/Kolkata
POLICY = {
    "enabled": True,
    "duration_minutes": 30,
    "time_zone": "Asia/Kolkata",
    "notice_minutes": 60,
    "calendar_ids": ["primary"],
}


class Provider:
    def __init__(self, intent):
        self.intent = intent

    def complete(self, message, **kwargs):
        if "action-safety router" in message:
            return json.dumps(
                {
                    "decision": "AUTO_ACT",
                    "confidence": 1,
                    "reason": "Configured interview scheduling",
                }
            )
        if "obligation extractor" in message:
            return json.dumps({"creates_obligation": False, "confidence": 1})
        return json.dumps(self.intent)


class Calendar:
    def __init__(self, busy=None):
        self.busy = busy or []
        self.calls = []
        self.error = False

    def freebusy(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "calendars": {
                calendar_id: (
                    {"errors": [{"reason": "notFound"}]} if self.error else {"busy": self.busy}
                )
                for calendar_id in kwargs["calendar_ids"]
            }
        }


@pytest.fixture
def setup(monkeypatch):
    calendar = Calendar()
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *args: calendar)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setattr(scheduling, "datetime", Clock)
    automation = make_automation()
    automation.action_config = {**automation.action_config, "calendar_scheduling": dict(POLICY)}
    gmail = FakeGmail()
    return SimpleNamespace(calendar=calendar, automation=automation, gmail=gmail)


def prepare(setup, intent, policy=None):
    if policy:
        setup.automation.action_config["calendar_scheduling"].update(policy)
    return scheduling.prepare_reply(
        automation=setup.automation,
        message=setup.gmail.source,
        provider=Provider(intent),
        db=None,
        settings=Settings(),
        now=NOW,
    )


def test_missing_time_generates_free_slots_without_model_inventing_availability(setup):
    setup.calendar.busy = [
        {"start": "2026-10-26T10:00:00+05:30", "end": "2026-10-26T11:00:00+05:30"}
    ]
    body, evidence = prepare(setup, {"kind": "no_time"})
    assert len(evidence["slots"]) == 3
    assert "11:00–11:30" in body and "Asia/Kolkata" in body
    assert "10:00–10:30" not in body
    assert "no calendar booking has been made" in body
    assert setup.calendar.calls[0]["calendar_ids"] == ["primary"]


def test_free_proposed_time_uses_user_default_duration(setup):
    body, evidence = prepare(
        setup,
        {
            "kind": "proposed",
            "timezone_explicit": True,
            "slots": [{"start": "2026-10-26T14:00:00+05:30"}],
        },
    )
    assert evidence["proposed_available"] is True
    assert "14:00–14:30" in body
    assert len(evidence["slots"]) == 1


def test_busy_proposed_time_offers_checked_alternatives(setup):
    setup.calendar.busy = [
        {"start": "2026-10-26T14:00:00+05:30", "end": "2026-10-26T15:00:00+05:30"}
    ]
    body, evidence = prepare(
        setup,
        {
            "kind": "proposed",
            "timezone_explicit": True,
            "slots": [{"start": "2026-10-26T14:00:00+05:30", "end": "2026-10-26T14:30:00+05:30"}],
        },
    )
    assert "conflicts with my availability" in body
    assert "14:00–14:30" not in body
    assert evidence["proposed_available"] is False


def test_date_only_request_keeps_every_suggestion_on_the_requested_date(setup):
    _, evidence = prepare(setup, {"kind": "date_only", "dates": ["2026-10-28"]})
    assert all("2026-10-28" in item["start"] for item in evidence["slots"])


@pytest.mark.parametrize(
    "intent",
    [
        {"kind": "unclear"},
        {"kind": "not_scheduling"},
        {"kind": "no_time", "dates": ["2026-10-28"]},
        {"kind": "date_only"},
        {"kind": "proposed", "slots": [{"start": "2026-10-26T14:00:00+05:30"}]},
        {
            "kind": "proposed",
            "timezone_explicit": True,
            "slots": [{"start": "2026-10-26T20:00:00+05:30"}],
        },
        {
            "kind": "proposed",
            "timezone_explicit": True,
            "slots": [{"start": "2026-10-25T14:00:00+05:30"}],
        },
    ],
)
def test_unclear_or_out_of_policy_requests_do_not_query_or_send(setup, intent):
    with pytest.raises((scheduling.SchedulingReview, ValueError)):
        prepare(setup, intent)
    assert not setup.calendar.calls and setup.gmail.send_calls == 0


def test_timezone_assumption_requires_explicit_user_option(setup):
    intent = {"kind": "proposed", "slots": [{"start": "2026-10-26T14:00:00+05:30"}]}
    body, _ = prepare(setup, intent, {"assume_timezone": True})
    assert "14:00" in body
    intent["slots"][0]["start"] = "2026-10-26T14:00:00Z"
    with pytest.raises(scheduling.SchedulingReview):
        prepare(setup, intent)


def test_error_or_busy_calendar_never_means_free(setup):
    setup.calendar.error = True
    with pytest.raises(scheduling.SchedulingReview):
        prepare(setup, {"kind": "no_time"})
    setup.calendar.error = False
    setup.calendar.busy = [{"start": NOW.isoformat(), "end": "2026-11-05T00:00:00Z"}]
    with pytest.raises(scheduling.SchedulingReview, match="No free slots"):
        prepare(setup, {"kind": "no_time"})


def test_selected_calendar_errors_and_malformed_intervals_fail_closed(setup):
    policy = scheduling.SchedulingPolicy.model_validate(
        {**POLICY, "calendar_ids": ["primary", "other"]}
    )

    class Missing:
        def freebusy(self, **kwargs):
            return {"calendars": {"primary": {"busy": []}}}

    with pytest.raises(scheduling.SchedulingReview):
        scheduling.busy_periods(Missing(), policy, NOW, NOW.replace(hour=20))
    setup.calendar.busy = [{"start": "2026-10-26T10:00:00", "end": "2026-10-26T11:00:00"}]
    with pytest.raises(ValueError):
        prepare(setup, {"kind": "no_time"})


def test_adjacent_busy_end_is_free_and_recheck_detects_a_new_conflict(setup):
    setup.calendar.busy = [
        {"start": "2026-10-26T09:30:00+05:30", "end": "2026-10-26T10:00:00+05:30"}
    ]
    _, evidence = prepare(setup, {"kind": "no_time"})
    assert evidence["slots"][0]["start"] == "2026-10-26T04:30:00+00:00"
    scheduling.recheck(evidence, None, Settings(), now=NOW)
    setup.calendar.busy = [evidence["slots"][0]]
    with pytest.raises(scheduling.SchedulingReview, match="now busy"):
        scheduling.recheck(evidence, None, Settings(), now=NOW)


@pytest.mark.parametrize(
    "policy,external,expected",
    [
        ("DRAFT_ONLY", True, "DRAFT_READY"),
        ("REQUIRED", True, "AWAITING_APPROVAL"),
        ("AUTO", False, "BLOCKED_BY_POLICY"),
        ("AUTO", True, "VERIFIED_SUCCESS"),
    ],
)
def test_executor_policies_and_duplicate_send_guard(setup, policy, external, expected):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        automation = setup.automation
        automation.approval_policy = policy
        db.add(automation)
        db.commit()
        executor = AutomationExecutor(
            settings=Settings(enable_external_actions=external),
            db=db,
            provider=Provider({"kind": "no_time"}),
            gmail=setup.gmail,
        )
        result = executor.run(automation=automation)
        assert result["decision"] == expected or result.get("verification_status") == expected
        assert setup.gmail.send_calls == (1 if expected == "VERIFIED_SUCCESS" else 0)
        if expected == "VERIFIED_SUCCESS":
            assert len(setup.calendar.calls) == 2
            assert executor.run(automation=automation)["decision"] == "ALREADY_EXECUTED"
            assert setup.gmail.send_calls == 1
    engine.dispose()


def test_approval_rechecks_calendar_and_rejects_changes_to_validated_draft(setup):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(
            settings=Settings(enable_external_actions=True),
            db=db,
            provider=Provider({"kind": "no_time"}),
            gmail=setup.gmail,
        )
        result = executor.run(automation=setup.automation)
        execution = db.get(AutomationExecution, result["execution"]["id"])
        assert (
            executor.approve(execution=execution, edited_draft="Actually any time is fine")[
                "decision"
            ]
            == "SCHEDULING_REGENERATION_REQUIRED"
        )
        setup.calendar.busy = [execution.output["calendar_scheduling"]["slots"][0]]
        assert executor.approve(execution=execution)["decision"] == "FALLBACK_NOTIFICATION"
        assert setup.gmail.send_calls == 0
    engine.dispose()


def test_calendar_failure_in_auto_mode_notifies_instead_of_sending(setup):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        setup.automation.approval_policy = "AUTO"
        db.add(setup.automation)
        db.commit()
        setup.calendar.error = True
        executor = AutomationExecutor(
            settings=Settings(enable_external_actions=True),
            db=db,
            provider=Provider({"kind": "no_time"}),
            gmail=setup.gmail,
        )
        assert executor.run(automation=setup.automation)["decision"] == "FALLBACK_NOTIFICATION"
        assert setup.gmail.send_calls == 0
    engine.dispose()


@pytest.mark.parametrize(
    "changes",
    [
        {"time_zone": "Not/AZone"},
        {"work_start": "18:00", "work_end": "09:00"},
        {"weekdays": []},
        {"weekdays": [0, 0]},
        {"weekdays": [7]},
        {"duration_minutes": 0},
        {"calendar_ids": [""]},
        {"horizon_days": 30},
    ],
)
def test_invalid_policies_are_rejected(changes):
    with pytest.raises(ValueError):
        scheduling.SchedulingPolicy.model_validate({**POLICY, **changes})


def test_save_validation_restricts_scheduling_to_gmail_replies():
    from fastapi import HTTPException

    from app.routes.automations import _validate_logic

    values = {
        "source": "gmail",
        "trigger_type": "NEW_EMAIL",
        "action_config": {"type": "DRAFT_REPLY", "calendar_scheduling": dict(POLICY)},
    }
    _validate_logic(values)
    with pytest.raises(HTTPException):
        _validate_logic(
            {**values, "action_config": {"type": "NOTIFY", "calendar_scheduling": dict(POLICY)}}
        )
    with pytest.raises(HTTPException):
        _validate_logic(
            {
                **values,
                "action_config": {
                    "type": "DRAFT_REPLY",
                    "calendar_scheduling": {**POLICY, "time_zone": "Bad"},
                },
            }
        )


def test_only_selected_weekdays_and_notice_window_are_offered(setup):
    _, evidence = prepare(setup, {"kind": "no_time"}, {"weekdays": [2], "notice_minutes": 60})
    for row in evidence["slots"]:
        start = datetime.fromisoformat(row["start"])
        assert start.astimezone(scheduling.ZoneInfo("Asia/Kolkata")).weekday() == 2
        assert start > NOW


def test_daylight_saving_skips_nonexistent_local_slots(setup):
    now = datetime(2026, 3, 8, 5, 0, tzinfo=UTC)
    setup.automation.action_config["calendar_scheduling"] = {
        **POLICY,
        "time_zone": "America/New_York",
        "weekdays": [6],
        "work_start": "01:00",
        "work_end": "04:00",
        "notice_minutes": 0,
        "horizon_days": 1,
    }
    _, evidence = scheduling.prepare_reply(
        automation=setup.automation,
        message=setup.gmail.source,
        provider=Provider({"kind": "no_time"}),
        db=None,
        settings=Settings(),
        now=now,
    )
    zone = scheduling.ZoneInfo("America/New_York")
    assert all(
        datetime.fromisoformat(row["start"]).astimezone(zone).hour != 2 for row in evidence["slots"]
    )


def test_buffer_and_alternatives_stay_near_requested_time(setup):
    setup.calendar.busy = [{"start": "2026-10-28T14:00:00+05:30", "end": "2026-10-28T15:00:00+05:30"}]
    body, evidence = prepare(setup, {
        "kind": "proposed", "timezone_explicit": True,
        "slots": [{"start": "2026-10-28T14:00:00+05:30"}],
    }, {"buffer_minutes": 60, "book_proposed": True})
    assert evidence["booking_candidate"] is None
    starts = [datetime.fromisoformat(row["start"]).astimezone(scheduling.ZoneInfo("Asia/Kolkata")) for row in evidence["slots"]]
    assert starts[0].hour == 16
    assert all(row.date().isoformat() == "2026-10-28" for row in starts)
    assert "no calendar booking has been made" in body


class BookingCalendar(Calendar):
    def __init__(self):
        super().__init__()
        self.events = {}
        self.inserts = 0
        self.readback_failure = False

    def get_event(self, *, event_id, **kwargs):
        from googleapiclient.errors import HttpError
        if self.readback_failure and self.inserts:
            raise RuntimeError("Readback unavailable")
        if event_id not in self.events:
            raise HttpError(SimpleNamespace(status=404, reason="Not Found"), b"{}")
        return self.events[event_id]

    def create_event(self, *, event, **kwargs):
        assert kwargs["calendar_id"] == "primary" and kwargs["send_updates"] == "none"
        self.inserts += 1
        self.events[event["id"]] = event
        return event


@pytest.mark.parametrize("policy,external,booked,sent", [
    ("DRAFT_ONLY", True, 0, 0), ("REQUIRED", True, 0, 0),
    ("AUTO", False, 0, 0), ("AUTO", True, 1, 1),
])
def test_combined_booking_respects_policy_and_verifies_before_send(setup, monkeypatch, policy, external, booked, sent):
    calendar = BookingCalendar()
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr("app.automation.scheduling_booking.get_calendar_client", lambda *a: calendar)
    setup.automation.approval_policy = policy
    setup.automation.action_config["calendar_scheduling"]["book_proposed"] = True
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    original_send = setup.gmail.send_reply
    def send(**kwargs):
        assert calendar.inserts == 1
        assert "I have added this time to my calendar" in kwargs["body_text"]
        return original_send(**kwargs)
    setup.gmail.send_reply = send
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(settings=Settings(enable_external_actions=external), db=db,
            provider=Provider({"kind": "proposed", "timezone_explicit": True,
                "slots": [{"start": "2026-10-28T14:00:00+05:30"}]}), gmail=setup.gmail)
        result = executor.run(automation=setup.automation)
        assert calendar.inserts == booked and setup.gmail.send_calls == sent
        if sent:
            assert result["verification_status"] == "VERIFIED_SUCCESS"
            assert result["execution"]["output"]["calendar_booking"]["status"] == "VERIFIED_SUCCESS"
            assert executor.run(automation=setup.automation, force=True)["decision"] == "ALREADY_EXECUTED"
            assert calendar.inserts == 1 and setup.gmail.send_calls == 1
        if policy == "REQUIRED":
            execution = db.get(AutomationExecution, result["execution"]["id"])
            assert executor.approve(execution=execution)["verification_status"] == "VERIFIED_SUCCESS"
            assert calendar.inserts == setup.gmail.send_calls == 1
    engine.dispose()


@pytest.mark.parametrize("failure", ["readback", "email"])
def test_partial_booking_never_sends_false_success_or_rebooks_on_force(setup, monkeypatch, failure):
    calendar = BookingCalendar()
    calendar.readback_failure = failure == "readback"
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr("app.automation.scheduling_booking.get_calendar_client", lambda *a: calendar)
    if failure == "email":
        setup.gmail.send_reply = lambda **kw: (_ for _ in ()).throw(RuntimeError("Transport unavailable"))
    setup.automation.approval_policy = "AUTO"
    setup.automation.action_config["calendar_scheduling"]["book_proposed"] = True
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(settings=Settings(enable_external_actions=True), db=db,
            provider=Provider({"kind": "proposed", "timezone_explicit": True,
                "slots": [{"start": "2026-10-28T14:00:00+05:30"}]}), gmail=setup.gmail)
        result = executor.run(automation=setup.automation)
        assert result["execution"]["status"] in {"EXECUTION_FAILED", "FALLBACK_NOTIFICATION"}
        assert result["execution"]["output"]["calendar_booking"]["status"] == ("PENDING" if failure == "readback" else "VERIFIED_SUCCESS")
        assert calendar.inserts == 1 and setup.gmail.send_calls == 0
        assert executor.run(automation=setup.automation, force=True)["decision"] == "EXISTING_EXECUTION"
        assert calendar.inserts == 1
    engine.dispose()


@pytest.mark.parametrize("intent,busy", [
    ({"kind": "no_time"}, []),
    ({"kind": "date_only", "dates": ["2026-10-28"]}, []),
    ({"kind": "proposed", "timezone_explicit": True, "slots": [{"start": "2026-10-28T14:00:00+05:30"}]}, [{"start": "2026-10-28T14:00:00+05:30", "end": "2026-10-28T15:00:00+05:30"}]),
    ({"kind": "proposed", "timezone_explicit": True, "slots": [{"start": "2026-10-28T14:00:00+05:30"}, {"start": "2026-10-28T16:00:00+05:30"}]}, []),
])
def test_combined_mode_does_not_book_unconfirmed_alternatives(setup, monkeypatch, intent, busy):
    calendar = BookingCalendar()
    calendar.busy = busy
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr("app.automation.scheduling_booking.get_calendar_client", lambda *a: calendar)
    setup.automation.approval_policy = "AUTO"
    setup.automation.action_config["calendar_scheduling"]["book_proposed"] = True
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(settings=Settings(enable_external_actions=True), db=db, provider=Provider(intent), gmail=setup.gmail)
        result = executor.run(automation=setup.automation)
        assert result["verification_status"] == "VERIFIED_SUCCESS"
        assert calendar.inserts == 0 and setup.gmail.send_calls == 1
        assert "no calendar booking has been made" in setup.gmail.messages["sent-1"].body
    engine.dispose()


def test_booking_approval_rechecks_new_conflict_before_insert(setup, monkeypatch):
    calendar = BookingCalendar()
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr("app.automation.scheduling_booking.get_calendar_client", lambda *a: calendar)
    setup.automation.action_config["calendar_scheduling"]["book_proposed"] = True
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(settings=Settings(enable_external_actions=True), db=db,
            provider=Provider({"kind": "proposed", "timezone_explicit": True, "slots": [{"start": "2026-10-28T14:00:00+05:30"}]}), gmail=setup.gmail)
        result = executor.run(automation=setup.automation)
        execution = db.get(AutomationExecution, result["execution"]["id"])
        calendar.busy = execution.output["calendar_scheduling"]["slots"]
        assert executor.approve(execution=execution)["decision"] == "FALLBACK_NOTIFICATION"
        assert calendar.inserts == 0 and setup.gmail.send_calls == 0
    engine.dispose()


def test_busy_alternatives_preserve_explicit_meeting_duration(setup):
    setup.calendar.busy = [{"start": "2026-10-28T14:00:00+05:30", "end": "2026-10-28T15:00:00+05:30"}]
    _, evidence = prepare(setup, {"kind": "proposed", "timezone_explicit": True,
        "slots": [{"start": "2026-10-28T14:00:00+05:30", "end": "2026-10-28T15:30:00+05:30"}]})
    assert all((datetime.fromisoformat(row["end"]) - datetime.fromisoformat(row["start"])).total_seconds() == 90 * 60 for row in evidence["slots"])


def test_scheduling_one_hour_default_and_explicit_range_precedence(setup):
    setup.automation.action_config["calendar_scheduling"].pop("duration_minutes", None)
    body, evidence = prepare(setup, {"kind": "proposed", "timezone_explicit": True, "slots": [{"start": "2026-10-28T15:00:00+05:30"}]})
    assert "15:00–16:00" in body
    body, evidence = prepare(setup, {"kind": "proposed", "timezone_explicit": True, "slots": [{"start": "2026-10-28T15:00:00+05:30", "end": "2026-10-28T17:00:00+05:30"}]})
    assert "15:00–17:00" in body


@pytest.mark.parametrize("lose_link", [False, True])
def test_combined_booking_preserves_the_original_meeting_link(setup, monkeypatch, lose_link):
    from dataclasses import replace

    link = "https://meet.google.com/abc-defg-hij?authuser=1"
    setup.gmail.source = replace(setup.gmail.source, body="Interview at 2 PM. Join: " + link)
    setup.gmail.messages[setup.gmail.source.id] = setup.gmail.source
    calendar = BookingCalendar()
    if lose_link:
        original = calendar.get_event
        calendar.get_event = lambda **kwargs: {**original(**kwargs), "description": "Dropped", "location": ""}
    monkeypatch.setattr(scheduling, "get_calendar_client", lambda *a: calendar)
    monkeypatch.setattr("app.automation.scheduling_booking.get_calendar_client", lambda *a: calendar)
    setup.automation.approval_policy = "AUTO"
    setup.automation.action_config["calendar_scheduling"]["book_proposed"] = True
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(setup.automation)
        db.commit()
        executor = AutomationExecutor(
            settings=Settings(enable_external_actions=True), db=db,
            provider=Provider({"kind": "proposed", "timezone_explicit": True,
                "slots": [{"start": "2026-10-28T14:00:00+05:30"}]}), gmail=setup.gmail,
        )
        result = executor.run(automation=setup.automation)
        event = next(iter(calendar.events.values()))
        assert event["location"] == link and link in event["description"]
        if lose_link:
            assert result["execution"]["status"] == "FALLBACK_NOTIFICATION"
            assert calendar.inserts == 1 and setup.gmail.send_calls == 0
        else:
            assert result["verification_status"] == "VERIFIED_SUCCESS"
            assert calendar.inserts == setup.gmail.send_calls == 1
    engine.dispose()
