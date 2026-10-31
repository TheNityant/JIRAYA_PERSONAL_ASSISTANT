# Gmail → Calendar → Meet attendance

## Source review and integration boundary

Adapted from `Meet_feature` at `4f3d7c0ea796a7e5691d94e7d676e4006739b7be`.
That branch is an unrelated root containing an older application snapshot. It was
not merged wholesale: that would remove current Calendar defaults/link handling,
background-worker health, account-scoped device pairing and the first-use guide.
Screenshots, generated audio, browser profiles, credentials-named files, conflict
backups and bundled DLLs were excluded from the integrated source.

Its five focused Meet test files initially produced 41 passes and 12 failures.
The companion factory returned a dictionary instead of an ASGI app; scheduling
tests referred to an unimplemented `SCHEDULE_MEET` action; the browser wait method
was nested inside another method; and the manager required inference credentials
just to initialize state. The integration keeps the existing supported **Create
calendar event** action and schedules attendance from the resulting real Calendar
event, rather than introducing a second, disconnected event-creation path.

The repaired joiner validates an exact HTTPS Meet host and full meeting-code path,
confirms camera/microphone are off, separates knocking from verified admission,
closes browser resources on failures and supports stopping a call. It does not
unmute based on guessed speech duration. Imported speech/voice experiments remain
isolated from the production attendance runtime; no model-license acceptance is
performed automatically. Recording, transcription, summaries and voice replies
are not enabled by the Meet page.

## Setup

1. Deploy the updated backend on Render and frontend on Vercel. Keep the existing
   OAuth setup, stable database and encryption key. No new Google OAuth client or
   dedicated GPU is required for this browser attendance path.
2. Install `extensions/jiraya-workspace/jiraya-workspace-0.5.0.vsix` in desktop VS
   Code. Save work and stop an older helper from its original terminal; if it was
   started detached by an old extension, restarting the computer once clears it.
3. Run **Jiraya: Set Up Local Companion**. Approve helper setup, select Python 3.12+
   if needed, and allow the Chromium download. Git remains required for checkpoints.
4. Pair the computer through **GitHub → Connect with VS Code** if not already paired.
   In **Meet**, explicitly enable automatic attendance for the primary Calendar.
5. For the first due meeting, sign in manually to the separate Google browser
   profile if prompted. Gmail/Calendar OAuth does not create that browser session.
   The Google account in this profile should match the Calendar account you intend
   to attend as. A host may need to admit the participant.

The frontend must use its same-origin `/api/jiraya` proxy. The local helper is not
installed on Render. Python/cloud dependencies remain separate from optional local
browser dependencies (`pip install -e '.[meet]'` for source-based setup).

## Runtime and persistence

- The device polls the cloud Calendar feed every 20 seconds while idle, including
  when the website is closed or its account session has logged out.
- The 30-day device credential is issued under a signed-in account, stored on the
  device, and accepted only under `/meet/device/*`. It cannot authorize other API
  routes. Pausing revokes this authorization when reachable and removes its local
  copy; expiry or revocation requires enabling again from the signed-in frontend.
- The feed contains eligible primary-Calendar events in a bounded next-day window.
  Cancelled, declined, free/transparent, all-day and ambiguous multi-link events are
  excluded. A verified Gmail-created event with one canonical Meet link is eligible.
- Automatic attempts occur from 30 seconds before start through two minutes after
  start. Events already ended are excluded. One browser meeting runs at a time;
  overlapping events are not joined concurrently.
- A claim keyed by account, Calendar event ID and start time is saved before
  opening the browser. A restart does not silently replay that claim. Failed or
  uncertain attempts may be explicitly retried while the source event is ongoing;
  the latest Calendar feed is consulted again, so removed/cancelled events are not
  joined from a stale queue. Verified completed meetings cannot be retried here.
- Browser profiles and visible attendance history are separated by cloud origin,
  Jiraya account and connected Calendar account. Changing the connected Calendar
  requires enabling attendance again. Browser cookies, evidence and local speech files stay on the device.
- Admission success requires in-call UI evidence. Knocking and sign-in requests
  are not reported as successful attendance. Meeting timing is interpreted with
  explicit offsets, and a call is bounded by the Calendar end time (maximum 4h).

The computer must stay awake, the helper and cloud backend must remain available,
and browser admission must succeed. This does not provide a cloud attendance bot
when the laptop is powered off. Google UI changes or sign-in restrictions can still
require intervention; fixture tests cannot establish live Google compatibility.

## Live acceptance test

Use a meeting you host with another account and a fresh meeting email.

1. Configure an enabled Gmail → Create calendar event rule with correct sender/
   subject conditions, Asia/Kolkata and 60 minutes, then select Automatic.
2. Send a new email proposing a meeting 5–10 minutes ahead with the exact Meet URL
   copied from Google Meet. Allow background processing; do not use Debug as proof
   of unattended execution.
3. Confirm one real Calendar event exists at the intended time, with the URL in
   Description/Location. Confirm it appears under Meet → Upcoming linked meetings.
4. Keep the device running and close the Jiraya tab. At the due time, complete the
   separate browser sign-in if required and admit the participant from the host.
5. Reopen Jiraya and confirm the device history shows JOINED while present and
   COMPLETED after leaving, rather than merely KNOCKING. Verify directly in Meet.
6. Reopen/restart the helper during a second controlled test and confirm it does
   not duplicate attendance. Retry only a known failed attempt after reviewing Meet.
7. Cancel or decline a future event; confirm no attempt. Pause attendance; confirm
   the current call stops and future joins are disabled. Test GitHub checkpoints
   afterward to verify that their pairing and project policies remain intact.

## Automated verification

`tests/test_meet_integration.py` covers scoped credentials after logout, revocation,
expiry, disabled actions, Calendar filtering, due-time bounds, restart deduplication,
protected local routes, retries, stop behavior and URL-host validation.
`test_gmail_calendar_meet_handoff_executes_once` in
`tests/test_gmail_automatic_calendar.py` exercises the real Gmail runtime, event
executor, Calendar feed and device dispatch with controlled provider/browser clients.

The updated browser regression covers Meet navigation, consent gating, enabling,
pausing and the existing account/pairing flows. The packaged-helper test extracts
and starts the actual VSIX payload. These checks do not join a live meeting or
establish Windows browser/login readiness.
