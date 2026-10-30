"""Monitoring health is evidence from a recent completed poll, not a saved flag."""

from datetime import UTC, datetime


def monitor_status(health: dict, *, enabled: bool, poll_seconds: int) -> dict:
    freshness_seconds = max(180, poll_seconds * 3)
    try:
        checked = datetime.fromisoformat(str(health.get("last_poll_at") or ""))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - checked).total_seconds()
        fresh = 0 <= age <= freshness_seconds
    except (ValueError, TypeError):
        fresh = False
    return {
        **health,
        "fresh": fresh,
        "stale_after_seconds": freshness_seconds,
        "operational": enabled
        and fresh
        and health.get("last_status") == "OK"
        and health.get("poll_source") != "manual",
    }
