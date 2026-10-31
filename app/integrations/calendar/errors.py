"""Safe, actionable diagnostics without OAuth tokens or provider payloads."""

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError


class CalendarConnectionError(RuntimeError):
    pass


def calendar_failure(exc):
    if isinstance(exc, CalendarConnectionError) or (isinstance(exc, RefreshError) and not getattr(exc, "retryable", False)):
        return "CALENDAR_RECONNECT_REQUIRED", "Reconnect Google Calendar in Connections, then retry this email. Its authorization is missing, expired, revoked or lacks required permissions."
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        if status == 401:
            return "CALENDAR_RECONNECT_REQUIRED", "Reconnect Google Calendar in Connections, then retry this email."
        if status == 403:
            return "CALENDAR_ACCESS_DENIED", "Google Calendar denied this request. Check Calendar API access and calendar permissions, then reconnect if needed."
    return "CALENDAR_UNAVAILABLE", "Calendar could not be reached or the event could not be verified. Check the Calendar connection and retry after reviewing execution details."
