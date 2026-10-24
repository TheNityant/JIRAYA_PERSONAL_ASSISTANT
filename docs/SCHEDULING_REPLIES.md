# Calendar-backed Gmail scheduling replies

This opt-in mode is implemented for incoming Gmail automations with action **Reply to email**. Previously, reply drafting did not query Calendar availability; creating an event was a separate action. This change does not enable scheduling on existing automations.

## Configure through the frontend

1. Connect both Gmail and Google Calendar to the same Jiraya account.
2. Create a Gmail automation matching the intended sender and scheduling emails. Use a narrow sender/subject filter for the first test.
3. Choose **Reply to email**, then enable **Check availability and suggest meeting times** under **Calendar-backed scheduling reply**.
4. Review the timezone, working hours, weekdays, default meeting duration, search horizon and minimum notice. Defaults are 30 minutes, weekdays, 09:00–18:00, seven days and two hours' notice; these are editable preferences, not inferred knowledge of your routine.
5. The default Calendar ID is `primary`. Add additional Google Calendar IDs if their busy periods should also block availability. Every selected calendar must be checked successfully. Free/busy reflects those calendars only; commitments missing from them cannot be known.
6. Leave **Use my scheduling timezone when the email omits one** off unless you explicitly want that assumption. The configured timezone is always used for Jiraya's own suggestions. Ambiguous proposed times otherwise require review.
7. Begin with **Draft only**, save and enable the automation. Scheduling mode produces a fixed availability reply rather than a free-form generated response. Personal context helps interpret the email; the structured scheduling fields determine the availability policy.

## Behavior

- A clear proposed time inside the configured working-hours/notice/date window is checked against all selected calendars. If free, the reply offers that time. A missing duration uses the configured default.
- If a valid proposed slot is busy, the reply offers up to three checked alternatives. It does not disclose event names or why you are busy.
- If the sender requests availability without any date/time constraints, Python finds up to three free slots inside your configured window. The model does not invent free slots.
- A date-only request limits suggestions to the extracted date(s). Unclear constraints, missing/ambiguous dates/timezones, out-of-policy proposed times, unavailable Calendar data and no matching slots create an action-required notification without sending.
- All-day and recurring conflicts are handled by Google's free/busy response. Provider/calendar errors and missing result entries are not treated as empty/free calendars.
- Availability is rechecked just before sending, including after approval. Changed availability, expired slots or changed scheduling settings block sending and require regeneration. Offered slots are not held or booked; there is still a race between the final check and a later change to Calendar.
- Editing a validated scheduling draft requires regeneration rather than silently sending different dates. To regenerate an existing Gmail execution, use **Debug · Run latest**, then **Regenerate draft** when the existing-execution result appears.

This mode proposes times only. It does not book Calendar events, send invitations, check the recruiter's calendar, monitor Calendar changes to send follow-up emails, or conduct a negotiation across messages automatically. A new incoming message can be processed by the normal Gmail automation.

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

## Tests and reference

`python -m pytest tests/test_scheduling_reply.py tests/test_companion_startup.py -q` covers scheduling policies, Calendar failures, timing boundaries, automatic/draft/approval paths, rechecking, duplicate sends and companion socket startup. From `web`, `node tests/scheduling-settings.cjs` tests frontend preferences and the Calendar context node using Playwright/Chromium without provider calls.

[Google Calendar free/busy reference](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query) defines busy intervals with inclusive starts and exclusive ends.
