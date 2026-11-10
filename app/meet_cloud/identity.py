"""Account-scoped browser readiness, independent of OAuth and worker heartbeat.

Only trusted worker/setup code writes this state. It contains no browser cookies,
passwords or tokens. CONNECTED means last verified, never a permanent login promise.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.meet_cloud.profiles import profile_key
from app.models import Notification
from app.services.notification_store import NotificationStore
from app.services.runtime_state_store import RuntimeStateStore

STATES = {"SETUP_REQUIRED", "CONNECTED", "RECONNECT_REQUIRED"}


def state_key(user_id, account):
    return "cloud-meet-browser:" + profile_key(user_id, account)


def browser_status(db, user_id, account):
    value = RuntimeStateStore(db).get(state_key(user_id, account)) if account else None
    value = value or {}
    state = value.get("state", "SETUP_REQUIRED")
    return {
        "state": state if state in STATES else "SETUP_REQUIRED",
        "last_verified_at": value.get("last_verified_at"),
        "updated_at": value.get("updated_at"),
        "requires_interactive_setup": state != "CONNECTED",
    }


def set_browser_status(db, user_id, account, state):
    if not account or state not in STATES:
        raise ValueError("A Calendar identity and valid browser state are required.")
    previous = browser_status(db, user_id, account)
    now = datetime.now(UTC).isoformat()
    verified_at = now if state == "CONNECTED" else previous["last_verified_at"]
    RuntimeStateStore(db).set(
        state_key(user_id, account),
        {"state": state, "last_verified_at": verified_at, "updated_at": now},
    )
    if state == "CONNECTED":
        for alert in db.scalars(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.source == "meet_cloud",
                Notification.category == "ACTION_REQUIRED",
                Notification.read_at.is_(None),
            )
        ).all():
            if (alert.notification_metadata or {}).get("browser_profile") == profile_key(
                user_id, account
            ):
                alert.read_at = datetime.now(UTC)
        db.commit()
        return
    # Stable until the next successful verification: repeated discovery cycles
    # or meeting failures produce one in-app alert, rather than one per poll.
    NotificationStore(db).create_once(
        user_id=user_id,
        category="ACTION_REQUIRED",
        severity="WARNING",
        title="Connect your meeting browser"
        if state == "SETUP_REQUIRED"
        else "Reconnect your meeting browser",
        message="Cloud attendance is waiting for Google browser sign-in. Your Calendar OAuth connection is separate. Open Meet to check setup; no password is requested by Jiraya.",
        source="meet_cloud",
        dedupe_key=state_key(user_id, account) + ":" + str(verified_at) + ":" + state,
        metadata={"browser_state": state, "browser_profile": profile_key(user_id, account)},
    )


def identity_mode(db, settings):
    """The worker's configured mode wins over a stale API environment setting."""
    heartbeat = RuntimeStateStore(db).get("cloud-meet-worker") or {}
    mode = heartbeat.get("identity_mode")
    return mode if mode in {"guest", "google_profile"} else settings.meet_cloud_identity_mode
