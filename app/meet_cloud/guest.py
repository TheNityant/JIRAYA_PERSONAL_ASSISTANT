"""Guest attendance: display identity is a label, never Google authentication."""

import re


def effective_settings(settings):
    if settings.meet_cloud_guest_only:
        return settings.model_copy(
            update={"meet_cloud_identity_mode": "guest", "meet_cloud_browser_portal_enabled": False}
        )
    return settings


def guest_name(user, connection=None):
    # Calendar titles are not necessarily personal names; prefer Jiraya's profile.
    candidates = [getattr(user, "display_name", ""), getattr(connection, "display_name", "")]
    for value in candidates:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if (
            value
            and value.casefold() not in {"local user", "primary", "calendar"}
            and "@" not in value
        ):
            return value[:60]
    email = getattr(user, "email", "") or getattr(connection, "provider_account_id", "")
    return email.split("@")[0][:60] or "Jiraya"
