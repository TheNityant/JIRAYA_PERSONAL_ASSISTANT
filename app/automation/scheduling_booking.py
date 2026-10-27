"""Book one checked, explicitly proposed time before sending its confirmation."""

import hashlib
from datetime import datetime

from googleapiclient.errors import HttpError

from app.automation.calendar_action import CalendarProposal
from app.automation.scheduling_reply import SchedulingReview, recheck
from app.integrations.calendar.connection import get_calendar_client


def book_before_reply(owner, execution, message, evidence):
    slot = evidence["slots"][0]
    if evidence.get("booking_candidate") != slot["start"] or len(evidence["slots"]) != 1:
        raise SchedulingReview("The booking no longer matches one explicitly proposed free time.")
    proposal = CalendarProposal.model_validate({
        "summary": message.subject[:255] or "Meeting",
        "description": "Created by Jiraya from a checked meeting request.",
        "start": slot["start"], "end": slot["end"],
    })
    event_id = hashlib.sha256(
        f"schedule:{execution.automation_id}:{execution.source_event_id}".encode()
    ).hexdigest()
    client = get_calendar_client(owner.db, owner.settings)

    def verify(event):
        private = (event.get("extendedProperties") or {}).get("private", {})
        return (
            event.get("id") == event_id and event.get("status") != "cancelled"
            and event.get("summary") == proposal.summary
            and private.get("jirayaAutomation") == execution.automation_id
            and private.get("jirayaSource") == execution.source_event_id
            and datetime.fromisoformat(event["start"]["dateTime"].replace("Z", "+00:00")) == proposal.start
            and datetime.fromisoformat(event["end"]["dateTime"].replace("Z", "+00:00")) == proposal.end
        )

    try:
        observed = client.get_event(calendar_id="primary", event_id=event_id)
    except HttpError as exc:
        if exc.resp.status != 404:
            raise
        observed = None
    if observed is None:
        recheck(evidence, owner.db, owner.settings)
        # Commit before the external write. An interrupted or uncertain booking must
        # be reviewed, rather than rerun as a new availability reply to the same email.
        execution = owner.executions.update(execution, output={
            **execution.output, "calendar_booking": {"status": "PENDING", "event_id": event_id},
        })
        try:
            client.create_event(calendar_id="primary", send_updates="none", event={
                **proposal.event(), "id": event_id,
                "extendedProperties": {"private": {
                    "jirayaAutomation": execution.automation_id,
                    "jirayaSource": execution.source_event_id,
                }},
            })
        except HttpError as exc:
            if exc.resp.status != 409:
                raise
        observed = client.get_event(calendar_id="primary", event_id=event_id)
    if not verify(observed):
        raise SchedulingReview("Calendar booking could not be verified. Review the event before retrying; no confirmation was sent.")
    body = execution.draft_body.replace(
        "Please confirm which time works for you. These are proposed times; no calendar booking has been made.",
        "I have added this time to my calendar. This reserves the time on my calendar; no Calendar invitation has been sent.",
    )
    return owner.executions.update(execution, draft_body=body, output={
        **execution.output, "calendar_booking": {
            "status": "VERIFIED_SUCCESS", "event_id": event_id,
            "html_link": observed.get("htmlLink"), "calendar_id": "primary",
        },
    })
