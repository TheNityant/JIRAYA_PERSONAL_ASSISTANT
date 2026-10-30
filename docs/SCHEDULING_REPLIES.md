# Calendar-backed Gmail scheduling replies

This opt-in mode is implemented for incoming Gmail automations with action **Reply to email**. Scheduling and optional booking must be explicitly enabled for each automation; existing rules remain unchanged.

## Configure through the frontend

1. Connect both Gmail and Google Calendar to the same Jiraya account.
2. Create a Gmail automation matching the intended sender and scheduling emails. Use a narrow sender/subject filter for the first test.
3. Choose **Reply to email**, then enable **Check availability and suggest meeting times** under **Calendar-backed scheduling reply**.
4. Review the timezone, working hours, weekdays, default meeting duration, search horizon and minimum notice. Defaults are 60 minutes, weekdays, 09:00–18:00, seven days and two hours' notice; these are editable preferences, not inferred knowledge of your routine.
5. The default Calendar ID is `primary`. Add additional Google Calendar IDs if their busy periods should also block availability. Every selected calendar must be checked successfully. Free/busy reflects those calendars only; commitments missing from them cannot be known.
6. Leave **Use my scheduling timezone when the email omits one** off unless you explicitly want that assumption. The configured timezone is always used for Jiraya's own suggestions. Ambiguous proposed times otherwise require review.
7. Optionally set **Gap around existing meetings** (0–120 minutes). Use 60 for an hour before and after existing busy intervals. Busy proposed times prefer later available alternatives on the requested date, then earlier ones; an unavailable requested date requires review rather than silently moving to another date.
8. Enable **Book one explicitly proposed free time, then reply** to reserve one clear sender-proposed slot on `primary` before confirming by email. Include `primary` among checked calendars. Busy proposals, date-only/no-time requests and multiple proposed choices only produce suggestions; Jiraya does not choose an unconfirmed alternative to book.
9. Begin with **Draft only**, save and enable the automation. Scheduling mode produces a fixed availability reply rather than a free-form generated response. Personal context helps interpret the email; the structured scheduling fields determine the availability policy.

## Behavior

- A clear proposed time inside the configured working-hours/notice/date window is checked against all selected calendars. If free, the reply offers that time; optional booking reserves a single proposed time after policy checks. A missing duration uses the configured default.
- If a valid proposed slot is busy, the reply offers up to three checked alternatives. It does not disclose event names or why you are busy.
- If the sender requests availability without any date/time constraints, Python finds up to three free slots inside your configured window. The model does not invent free slots.
- A date-only request limits suggestions to the extracted date(s). Unclear constraints, missing/ambiguous dates/timezones, out-of-policy proposed times, unavailable Calendar data and no matching slots create an action-required notification without sending.
- All-day and recurring conflicts are handled by Google's free/busy response. Provider/calendar errors and missing result entries are not treated as empty/free calendars.
- Availability is rechecked just before sending, including after approval. Changed availability, expired slots or changed scheduling settings block sending and require regeneration. Suggested alternatives are not held. Optional booking rechecks immediately before insertion and reads the event back before sending confirmation. Google Calendar does not provide an atomic free/busy-and-insert operation, so another actor can still create a conflicting event between those calls.
- Editing a validated scheduling draft requires regeneration rather than silently sending different dates. To regenerate an existing Gmail execution, use **Debug · Run latest**, then **Regenerate draft** when the existing-execution result appears.

This mode does not send Calendar invitations, check the recruiter's calendar, or initiate a follow-up just because a Calendar event changed. New incoming scheduling messages also reach configured scheduling rules in threads already tracked for obligations, with at most one matching scheduling reply rule claiming each message.

Booking uses a stable per-automation/message event ID and verifies event ownership, dates and summary. A pending/uncertain booking or booked event with a failed email cannot be force-regenerated; review Calendar and Sent mail first. The execution shows the booking separately from the email result. Calendar success alone never counts as a verified email send. Do not enable a separate Create calendar event rule for the same emails when using combined booking.

## Automatic sending

After testing, choose **Automatic** if you want matching replies sent without review. The existing action-safety router must permit that specific message, and the app owner's `ENABLE_EXTERNAL_ACTIONS` setting must allow sending. Calendar uncertainty still falls back to a notification. **Draft only** never sends; **Ask before action** waits for your approval and rechecks before sending. Successfully verified sends retain the existing duplicate-execution protection.

No live email or calendar action is performed by installing this change. Automated tests use fixture emails and calendars. A real OAuth connection, timezone interpretation and the configured model still need account-level validation.

## Test with your own two email accounts

Use a unique subject such as `JIRAYA-SCHEDULE-TEST`, a test sender filter and **Draft only**. Choose future working dates inside the configured horizon; older dates intentionally require review.

1. **No time:** send “I would like to arrange an interview. What times are you available?” Check that every proposed slot fits the configured hours, notice and timezone, and avoids Calendar busy periods.
2. **Free time:** send a future date, explicit start/end and timezone, for example “Can we interview on <future working date>, 14:00–14:30 Asia/Kolkata?” Verify that the draft offers that exact interval when free.
3. **Busy time:** mark that interval **Busy** in a selected test calendar, then send a new matching email. Expect alternatives rather than acceptance of the busy interval. Events marked Free do not block availability.
4. **Date only:** ask for availability on a particular future working date. Every suggested slot should be on that date.
5. **Ambiguity/error:** omit the proposed timezone while timezone assumption is off, or use an unavailable selected calendar. Expect a notification and no reply.
6. **Stale approval:** use **Ask before action**, generate a proposal, then mark one offered slot busy before approving. Sending should be blocked. This approval test requires external actions enabled and should use your own test recipient.

Install updated dependencies with `python -m pip install -e .`; this includes timezone data needed on Windows. Restart the backend and frontend. Existing provider credentials stay in the backend; no new OAuth client is needed, although an older Calendar connection missing free/busy scopes may need reconnecting.

## Simple Gmail → Create calendar event

For event creation without a reply, keep **Create calendar event** and set **Default event timezone** and **Default event minutes**. These defaults may fill a missing timezone or end time; the meeting date and start must still be clear. Existing rules can supply those defaults in context instead. New selections show editable timezone and 60-minute defaults. This action does not check free/busy; use the combined scheduling reply for conflict-aware booking.

Calendar failures now distinguish connection/reconnect problems, action-safety refusal, missing event details and write/readback failures. A permanently revoked refresh token marks Calendar as needing reconnection in Connections; a transient token-server failure does not invalidate consent. Reconnecting is required to restore a revoked token. For an External Google OAuth app still in Testing, refresh tokens for Calendar/Gmail scopes normally expire after seven days ([Google OAuth documentation](https://developers.google.com/identity/protocols/oauth2)).

## Live retest after updating

Restart backend/frontend, reconnect Calendar and confirm its events load. Verify Gmail monitoring is enabled and the app owner has enabled external actions if you want writes. Pause overlapping reply/event rules for the test. Send from a different account into the connected inbox, after saving/enabling the rule; the monitor deliberately ignores SENT messages, including self-sent tests labelled SENT. Wait two configured Gmail polling intervals. Debug Run latest defaults to the latest unread inbox message, so keep the test unread or use its message ID through the API.

Use subject `JIRAYA-CALENDAR-TEST` and sender filter for your test account. Set Asia/Kolkata, all relevant weekdays, suitable working hours, 30 minutes and a future date inside the horizon. First test Create calendar event with an email that explicitly includes date, start/end and timezone. Expect VERIFIED_SUCCESS and one actual event. For combined scheduling choose Reply to email, enable availability plus booking, and test a free proposed slot, a busy proposed slot, no time, and a new proposal in an existing thread. A free single proposal should create one event and one verified reply; conflicts or absent times should send only checked suggestions. With a 60-minute gap, an event ending 15:00 should block a new meeting before 16:00. Automatic still requires the action-safety check and external-action setting.

Fixtures do not establish success against your live Google account. Capture the execution decision, output reason and Gmail/Calendar monitor health if a live test fails; do not share tokens.

## Tests and reference

`python -m pytest tests/test_scheduling_reply.py tests/test_companion_startup.py -q` covers scheduling policies, Calendar failures, timing boundaries, automatic/draft/approval paths, rechecking, duplicate sends and companion socket startup. From `web`, `node tests/scheduling-settings.cjs` tests frontend preferences and the Calendar context node using Playwright/Chromium without provider calls.

[Google Calendar free/busy reference](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query) defines busy intervals with inclusive starts and exclusive ends.

### Event extraction defaults and diagnostics

Standalone Calendar event extraction now applies saved timezone and duration defaults in Python after the model extracts the date/start. A local ISO start plus `end: null` is sufficient when both explicit defaults exist. Explicit offsets and end times from the email retain precedence. Missing dates, missing start times, ambiguous clock-change times and missing defaults still stop creation. The exact support context wording beginning `My timezone is Asia/Kolkata` and specifying a `default duration of 30 minutes` is recognized for existing rules; structured event timezone/minutes fields remain preferred.

Calendar JSON extraction uses a bounded 1024-token response; ordinary model calls retain their 256-token budget. Truncated responses are rejected. The execution panel distinguishes model-service failure, invalid JSON, invalid event fields, invalid defaults and missing event details, and shows which defaults were loaded. No raw model response or provider error payload is exposed. Updating instructions alone cannot repair a provider or model-output error; retry after updating/restarting and inspect the new diagnostic.

### One-hour fallback duration

When the email omits an end time/duration and no custom rule duration is saved, standalone event creation and scheduling replies use 60 minutes. Explicit email ranges/durations take precedence (for example 15:00–17:00 remains two hours). Existing rules with an explicitly saved 30-minute duration or a context instruction requesting 30 minutes retain that customization; set Default event minutes or Default meeting minutes to 60 once to update those rules. Structured duration settings take precedence over context defaults.
