from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class CalendarClient:
    def __init__(self, credentials: Credentials):
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def list_calendars(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = self.service.calendarList().list(pageToken=page_token).execute()
            items.extend(response.get("items", []) or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def get_primary_calendar(self) -> dict[str, Any]:
        calendars = self.list_calendars()
        primary = next((item for item in calendars if item.get("primary")), None)
        if primary:
            return primary
        if calendars:
            return calendars[0]
        raise RuntimeError("Google Calendar returned no calendars for this account.")

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: str,
        time_max: str | None = None,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "maxResults": max(1, min(max_results, 100)),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max:
            kwargs["timeMax"] = time_max
        response = self.service.events().list(**kwargs).execute()
        return list(response.get("items", []) or [])

    def get_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        return self.service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    def changes(self, *, calendar_id: str = "primary", sync_token: str | None = None) -> tuple[list[dict], str]:
        items: list[dict] = []
        page_token = None
        while True:
            options = {"calendarId": calendar_id, "maxResults": 2500, "showDeleted": True}
            if sync_token:
                options["syncToken"] = sync_token
            if page_token:
                options["pageToken"] = page_token
            response = self.service.events().list(**options).execute()
            items.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                token = response.get("nextSyncToken")
                if not token:
                    raise RuntimeError("Calendar did not return a sync token")
                return items, token

    def freebusy(
        self,
        *,
        time_min: str,
        time_max: str,
        calendar_ids: list[str],
        time_zone: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": item} for item in (calendar_ids or ["primary"])],
        }
        if time_zone:
            body["timeZone"] = time_zone
        return self.service.freebusy().query(body=body).execute()

    def create_event(
        self,
        *,
        calendar_id: str,
        event: dict[str, Any],
        send_updates: str = "none",
    ) -> dict[str, Any]:
        return (
            self.service.events()
            .insert(calendarId=calendar_id, body=event, sendUpdates=send_updates)
            .execute()
        )

    def patch_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        changes: dict[str, Any],
        send_updates: str = "none",
    ) -> dict[str, Any]:
        return (
            self.service.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=changes,
                sendUpdates=send_updates,
            )
            .execute()
        )

    def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        send_updates: str = "none",
    ) -> None:
        (
            self.service.events()
            .delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates=send_updates,
            )
            .execute()
        )
