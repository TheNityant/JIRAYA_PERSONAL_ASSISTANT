"""Apply trusted event defaults after extraction, without inventing missing dates."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter


DEFAULT_MEETING_MINUTES = 60


class EventDetailsRequired(ValueError):
    pass


def event_defaults(automation):
    config = automation.action_config or {}
    context = str((automation.context_config or {}).get("instructions") or "")
    zone = config.get("event_time_zone")
    duration = config.get("event_duration_minutes")
    # Compatibility with the explicit context wording suggested in the UI/support.
    # Never extract defaults from the external email or infer the machine timezone.
    if not zone:
        zones = set(re.findall(r"\bMy timezone is\s+([A-Za-z_]+/[A-Za-z_/-]+)", context, re.I))
        if len(zones) == 1:
            zone = zones.pop()
    if duration is None:
        durations = set(re.findall(r"\bdefault(?: meeting)? duration(?:\s+of|\s*:)?\s+(\d+)\s+minutes\b", context, re.I))
        if len(durations) > 1:
            raise EventDetailsRequired("Conflicting default durations need review. Set Default event minutes explicitly.")
        if len(durations) == 1:
            duration = int(durations.pop())
    if duration is None:
        duration = DEFAULT_MEETING_MINUTES
    if zone:
        ZoneInfo(zone)
    if duration is not None and (type(duration) is not int or not 15 <= duration <= 120):
        raise EventDetailsRequired("Set Default event minutes to a value from 15 to 120.")
    return zone, duration


def apply_event_defaults(parsed, *, zone, duration):
    if not isinstance(parsed, dict):
        raise ValueError("Expected event JSON object")
    if "error" in parsed:
        raise EventDetailsRequired("The model could not extract a clear event. Check the email date/start time and set an explicit default timezone and duration.")
    result = dict(parsed)

    def parse_time(value):
        if not isinstance(value, str) or not re.search(r"[T ]\d{2}:\d{2}", value):
            raise EventDetailsRequired("The event needs an explicit date and start time; a date alone cannot be booked.")
        stamp = TypeAdapter(datetime).validate_python(value)
        if stamp.tzinfo is None:
            if not zone:
                raise EventDetailsRequired("Set Default event timezone (for example Asia/Kolkata), or include an explicit timezone in the email.")
            timezone = ZoneInfo(zone)
            first, second = stamp.replace(tzinfo=timezone, fold=0), stamp.replace(tzinfo=timezone, fold=1)
            if first.utcoffset() != second.utcoffset() or first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) != stamp:
                raise EventDetailsRequired("This local time is ambiguous or does not exist during a clock change. Include an explicit UTC offset.")
            stamp = first
        return stamp

    start = parse_time(result.get("start"))
    if result.get("end"):
        end = parse_time(result["end"])
    elif duration is not None:
        end = (start.astimezone(UTC) + timedelta(minutes=duration)).astimezone(start.tzinfo)
    else:
        raise EventDetailsRequired("Set Default event minutes, or include an end time/duration in the email.")
    result.update(start=start, end=end)
    return result
