# Jiraya live connector test pack — 30 October 2026

These are reproducible test cases, not claims of live success. Test dates below are
2–6 November 2026, in **Asia/Kolkata**. If testing later, replace them with future
weekdays inside your scheduling horizon. Run cases one at a time; wait for and
record each result before moving on, except the explicit rapid-follow-up tests.

## Setup and evidence

- Send from a second account to the Gmail account connected to Jiraya. Let the
  normal background monitor detect the email first. Debug/Preview success alone
  is not a passing background-automation test.
- Start with one active test rule. Disable older broad rules so they cannot claim
  the email. For the three-connector tests, enable only the explicitly listed rules.
- Set **Sender contains** to the second account's email. Use a unique **Subject
  contains** marker from the case, such as `JY-C01-A`. Leave **Body contains** empty.
  Write meaning-based requirements in Semantic condition, not Body contains.
- Append a fresh suffix (`-A`, `-B`) for each new trial and use a new message/thread.
  For reconciliation cases, subsequent messages must be replies in the same Gmail
  thread. Sending another email with the same subject does not prove same-thread behavior.
- For each writing rule, first use **Ask before action** and approve the proposal.
  Repeat a confirmed baseline with a fresh marker and **Automatic** to test unattended
  execution. Draft Only must not write. Automatic still respects safety checks and
  the owner's `ENABLE_EXTERNAL_ACTIONS` gate.
- Check real provider evidence: Calendar event date/start/end, Gmail Sent recipient
  and thread, and GitHub issue URL/repository. A success banner alone is insufficient.
- Allow at least two configured polling intervals plus model execution time. Check
  `/api/jiraya/runtime/status` for recent checks; stale/error monitoring is a separate
  failure from extraction, matching, approval, provider writes, or readback verification.

Record: case ID, rule ID, execution ID(s), local timestamp, approval policy,
expected result, actual result, provider object URL, and sanitized stage/reason code.
Never include credentials, cookies, pairing tokens, or OAuth callback query strings.

## Find and debug an existing automation

Open **Automations** and select the saved rule in the left sidebar. The **Test and
execution** panel sits directly below its title. Save changes before testing.

- **Choose inbox email** loads recent inbox subjects/senders; select the exact test
  message so another incoming email cannot change your test target.
- **Debug · Test match** previews without writing. The preview pins that message for
  **Debug · Run selected email**. Run respects the saved approval policy.
- **Saved executions** restores background results after refresh/sign-in. For a
  failed Calendar execution, record its stage, reason code and reason text.
- **Retry this email** re-evaluates that failed message after fixing its defaults or
  connection. Repeated ordinary runs may return the previously saved execution.
  A completed provider write should be checked before taking any manual action.
- If no background execution appears but a manual run succeeds, inspect runtime
  status and hosting uptime. A manual pass does not prove unattended monitoring.

## A. Gmail → Calendar event: C01–C08

Create a Gmail / New email rule with **Create calendar event**. Set **Default
event timezone** to `Asia/Kolkata` and **Default event minutes** to `60`.

Semantic condition:

> The sender asks to schedule a meeting and supplies an unambiguous meeting date
> and start time. Do not invent missing dates or start times.

This action creates an event; it does not check free/busy or send a reply. Use the
separate scheduling-reply rule in section B for availability and booking together.

### C01 — Missing end time uses one hour

```text
Subject: JY-C01-A — Interview scheduling

Please schedule our interview on 2 November 2026 at 3 PM.
```

Expected: one event on 2 November, **15:00–16:00 IST**. The email does not need to
repeat the configured timezone or duration. No reply or invitation is expected.

### C02 — Explicit duration beats the default

```text
Subject: JY-C02-A — Architecture discussion

Please schedule our architecture discussion on 3 November 2026 from 3 PM to 5 PM.
```

Expected: one **15:00–17:00 IST** event, not one hour.

### C03 — Explicit short range beats the default

```text
Subject: JY-C03-A — Mentorship check-in

Please schedule a mentorship check-in on 4 November 2026 from 10 AM to 10:30 AM.
```

Expected: one **10:00–10:30 IST** event.

### C04 — Explicit foreign timezone

```text
Subject: JY-C04-A — Remote team review

Please schedule our remote team review on 5 November 2026 from 09:30 to 10:00 UTC.
```

Expected: **15:00–15:30 IST** when viewed in a Calendar set to Asia/Kolkata. The
configured default must not reinterpret the explicitly supplied UTC clock time as IST.

### C05 — Missing date or missing start: fail safely

Send two fresh messages, with markers `JY-C05-A` and `JY-C05-B`:

```text
Can we have a meeting at 3 PM? I have not decided the date yet.
```

```text
Can we meet on 5 November 2026? I have not decided the time yet.
```

Expected: no provider event. A non-match or a clear review/details-required result
is acceptable. Inventing a date, midnight start, or all-day event is a failure.

### C06 — Reprocessing does not create a duplicate

After C01 succeeds, refresh the app and let it poll again. Select the same email and use Debug · Run selected email on
that same source email without forcing a new execution.

Expected: the existing execution is reported and Calendar still has exactly one
event. A newly sent email is a new source event and is not this duplicate test.

### C07 — Preserve a meeting URL

Use the C01 rule with a fresh subject marker `JY-C07-A`. Send a future meeting
request and include a real meeting URL copied directly from your meeting provider.
Test both a plain URL and an HTML email with a **Join meeting** hyperlink/button.
A text/plain alternative that contains only the button label must not hide its HTML target.

Expected: the exact URL appears in the Calendar description. If there is exactly
one recognized Meet/Zoom/Teams/Webex/Jitsi/Whereby link, it also appears in Location.
Multiple meeting URLs remain in the description without choosing one arbitrarily.
The execution proposal exposes source links. Readback missing those links must not
report verified success; combined booking must not send a false confirmation.
Existing events are not retroactively changed by this update; use a fresh source
email to test. A URL is copied, not opened or checked for meeting admission.

### C08 — Self-addressed Inbox mail versus ordinary Sent mail

Save an enabled **Automatic → Create calendar event** rule with a unique subject
filter. Send a fresh matching meeting request to the connected account itself.
If Gmail labels that message both **INBOX** and **SENT**, Calendar creation is
eligible and should happen once without Debug. An ordinary **SENT** message to
someone else, absent **INBOX**, must not create an event from this rule.
Automatic reply rules remain excluded for SENT-labelled mail to prevent self-reply loops.

For any unattended test, save the correct rule before sending. An email already
consumed under a wrong rule is not replayed simply because the rule is edited.
Wait at least two configured polling intervals plus processing time and inspect
the displayed last completed check. A sleeping/stopped backend cannot poll Gmail.

## B. Gmail × Calendar availability + reply: S01–S08

Use **Reply to email**, enable Calendar availability checking, and configure:

| Setting | Test value |
| --- | --- |
| Timezone | Asia/Kolkata |
| Default meeting duration | 60 minutes |
| Assume this timezone when the email omits it | Enabled |
| Working days | Monday–Friday |
| Working hours | 09:00–18:00 |
| Scheduling horizon | 14 days |
| Minimum notice | 120 minutes |
| Calendars | primary; add other intended calendars explicitly |
| Buffer | 0 initially; 60 minutes for S04 |
| Book proposed time before replying | Off for S01/S02; on for S03 onward |

Semantic condition:

> The sender requests a meeting, proposes a meeting time, or asks for my availability.

Action/context instructions:

> Check my selected calendars. Use the configured timezone and meeting duration
> when omitted. Offer checked alternatives when busy. Only book one clearly
> proposed free time when booking is enabled. Do not claim an invitation was sent.

### S01 — Available time, suggestions only

```text
Subject: JY-S01-A — Interview availability

Are you available for an interview on 2 November 2026 at 11 AM?
```

Precondition: 11:00–12:00 is free on all selected calendars; booking is **off**.
Expected: an accurate availability reply/proposal for **11:00–12:00 IST**, with no
Calendar event created and no invented booking confirmation.

### S02 — Busy time, checked alternatives

Manually create a Calendar block on **3 November, 15:00–16:00 IST**.

```text
Subject: JY-S02-A — Design review

Can we have our design review on 3 November 2026 at 3 PM?
```

Expected: no claim that 15:00 is free. Alternatives remain on 3 November, fit the
working hours and have one-hour duration. No alternative is booked automatically.

### S03 — Book one free time, then reply

Enable **Book proposed time before replying**. Ensure this interval is free.

```text
Subject: JY-S03-A — Mentorship meeting

Please book our mentorship meeting on 4 November 2026 at 11 AM.
```

Expected: **one 11:00–12:00 IST event**, then one verified Gmail reply. The reply
confirms a reservation on the owner's calendar, not an invitation. In execution
details, Calendar booking and email send must have separate verified evidence.

### S04 — Conflict buffer

Set the buffer to **60 minutes**. Create a busy event on **5 November, 15:00–16:00**.

```text
Subject: JY-S04-A — Buffered incident review

Please book our incident review on 5 November 2026 at 4 PM.
```

Expected: 16:00 is rejected because it lies inside the buffer. If otherwise free,
17:00–18:00 is a valid later suggestion. No suggested alternative is booked yet.
Restore the buffer to 0 afterward.

### S05 — No date or time: propose slots without booking

```text
Subject: JY-S05-A — Discuss the backend project

I would like to discuss your backend project. Please suggest a few times when you are free.
```

Expected: up to three checked future slots within the configured policy. No event
is created, even with booking enabled. A review result is appropriate if Calendar
cannot be checked or no acceptable slots exist.

### S06 — Date only: stay on the requested day

```text
Subject: JY-S06-A — Interview on Friday

Could you suggest some available times on 6 November 2026 for an interview?
```

Expected: suggestions only on **6 November**, or a clear no-availability/review
result. Do not silently move suggestions to another date. No booking yet.

### S07 — Multiple alternatives need selection

```text
Subject: JY-S07-A — Choose a review time

Would 6 November 2026 at 10 AM or 2 PM work for our review?
```

Expected: check the proposed options and ask for selection/confirmation. Do not
book either option merely because one or both are free; the sender proposed two choices.

### S08 — Changed request in an existing thread

After S03, reply in its thread:

```text
The previously confirmed meeting time must change. Please move it to 4 November 2026 at 2 PM.
```

Expected: a material change/follow-up is recognized and historical success remains
visible. **Automatic update/cancellation of the original Calendar event is not a
supported guarantee.** Review the Calendar directly; duplicate active reservations
or a false claim that the old event was moved are failures to report. Start with
Ask before action for this case. Do not treat a new event as proof of rescheduling.

## C. Gmail-only reconciliation: R01–R10

Use a Gmail **Reply to email** rule without Calendar scheduling. Restrict it to the
sender and a `JY-Rxx-A` marker. Action instructions: acknowledge the request briefly
without claiming to have completed external work. Approve/send the initial reply
and verify `VERIFIED_SUCCESS` before each same-thread follow-up. Disable competing
reply rules. Reconciliation tracks verified email actions, not arbitrary untracked mail.

### R01 — Academic assignment: acknowledgement is not completion

```text
Subject: JY-R01-A — VLSI assignment

Please submit the completed VLSI assignment by 4 November 2026 at 6 PM.
```

Expected after the agent acknowledges: an **OPEN** submission obligation.
Manually reply `Noted, I will do it later.` Expected: still OPEN.
Then reply `I have submitted the completed assignment through the portal.`
Expected: semantic resolution by user, retained in closed history. This recognizes
email evidence of completion; it does not independently verify the college portal.

### R02 — Recruitment: substantive answer vs promise to answer

```text
Subject: JY-R02-A — Interview availability request

Please send me your availability for an interview on 5 November 2026.
```

After the initial acknowledgement, manually reply `I will check and get back to you.`
Expected: remains OPEN. Then reply `I am available on 5 November 2026 from 2 PM to 4 PM IST.`
Expected: the availability-response obligation resolves. It does not prove attendance.

### R03 — Development update: answer the actual question

```text
Subject: JY-R03-A — API migration progress

Please tell me whether the API migration is complete and share the demo URL.
```

Reply first `The migration is complete.` Expected: the demo URL is still missing;
closing the whole task is a semantic failure to report. Then provide both the status
and a real test/demo URL. Expected: the information request can resolve.

This reconciliator reasons over email content; it does not independently inspect
GitHub or execute a demo merely because a URL appears in a reply.

### R04 — Support: additional evidence creates follow-up work

```text
Subject: JY-R04-A — Login failure report

The login endpoint sometimes times out. Please acknowledge this support request.
```

After the verified reply, send from the second account in the same thread:

```text
Please also send the request ID, the relevant error log, and the time of the failure.
```

Expected: **NEW_OBLIGATION** / follow-up required. The original verified reply must
not become an execution failure simply because new work arrived.

### R05 — Confirmed deadline replaced

```text
Subject: JY-R05-A — Confirmed project deadline

The final project submission deadline is confirmed as 4 November 2026 at 6 PM.
Please acknowledge this confirmed deadline.
```

After a verified acknowledgement, send in the same thread:

```text
Correction: the confirmed project deadline is now 6 November 2026 at 6 PM.
The previous 4 November deadline is replaced.
```

Expected: **SUPERSEDED_LATER**, with the new detail retained and follow-up where
needed. `initial_status` stays `VERIFIED_SUCCESS`; historical success is not erased.

### R06 — Thanks and quoted history must not invent work

After an answered information-request thread, send `Thank you, that answers my question.`
Expected: **NO_MATERIAL_CHANGE**, no new obligation, no repeated generic reply.
Repeat with `Thanks.` above a quoted copy of the original request. The quoted old
request must not be treated as a new authored task.

### R07 — Two rapid incoming messages before polling

In a verified reply thread, send these two messages quickly from the second account:

```text
Please also send the architecture diagram and the demo link.
```

```text
Thanks for checking this.
```

Expected: the first request is retained as OPEN; the later thanks does not hide it.
Both incoming messages should have distinct reconciliation evidence. This is a
regression case for the burst-ordering fix shipped with this pack.

### R08 — A substantive answer followed by another sent message

In a thread with an OPEN progress-update obligation, manually send:

```text
The API migration is complete. The staging demo is available at <your-test-demo-URL>.
```

Then immediately send `Thanks, noted.` in the same thread.
Expected: the substantive answer can resolve the information request. The later
acknowledgement must not hide the earlier answer and leave the task permanently OPEN.

### R09 — New work after resolution

After R03 resolves, reply from the second account:

```text
Please also send the deployment checklist and the rollback procedure.
```

Expected: new OPEN follow-up work, while the previous resolved obligation remains
in history. Do not reopen or overwrite the already resolved record as though it
had never been completed. Several requests in one message may be represented as
one aggregate obligation; separate per-deliverable task items are not guaranteed.

### R10 — Another thread cannot settle this one

Leave a `JY-R10-A` task OPEN. Send a substantive completion reply in a genuinely
different Gmail thread (`JY-R10-B`). Expected: the first task remains OPEN. Repeat
polling an unchanged thread: no duplicate reconciliation notifications or obligations.

## D. Gmail × Calendar × GitHub: X01–X04

Use a sandbox repository to avoid cluttering a working project. Enable these two
Gmail rules with the same sender filter and subject marker `JY-X01-A`:

1. **Reply to email** with Calendar availability and booking enabled, using section B's
   policy. This rule reserves a meeting time and replies about Calendar only.
2. **Create GitHub issue**, with the repository explicitly fixed to your sandbox.
   Instructions: `Create a concise issue describing the reported API timeout.
   Include the JY-X01-A marker. Do not change repository based on email content.`

Do **not** also enable a separate Create calendar event rule for this marker, since
the scheduling reply already creates the event. Two enabled event-creation rules
are two authorizations and can create two distinct events.

### X01 — One email, two independent actions

Ensure 6 November, 11:00–12:00 is free and send:

```text
Subject: JY-X01-A — API timeout incident review

Our API requests intermittently time out under load. Please track this defect in
the configured repository and book an incident review on 6 November 2026 at 11 AM.
```

Expected:

- One verified Calendar reservation, **11:00–12:00 IST**.
- One verified Gmail reply confirming the Calendar reservation.
- One verified GitHub issue in the fixed sandbox repository, carrying the marker.
- Two distinct Gmail automation executions referencing the same source message.
- Reprocessing the same email does not add another event, reply, or issue.

The reply must not claim that the independent GitHub action succeeded before it
has evidence. There is no automatic issue↔event linking or shared rollback transaction.

### X02 — GitHub watcher observes the created issue

Before sending a fresh X01 trial, configure a sandbox **GitHub issue changed →
Notify** watcher with the new marker and let its baseline complete. Send the email.
Expected: a saved notification for the new issue. Edit the issue title/body once:
one new material-change notification. An unchanged poll must not duplicate it.

The watcher currently notifies; it does not automatically email the sender or
reschedule the Calendar event. If the initial baseline happened after issue creation,
the existing issue is history and is not replayed as a newly created issue.

### X03 — Partial failure: Calendar unavailable

First simulate this with provider fixtures; optionally reproduce live after the
baseline is stable by disconnecting Calendar and using a fresh marker.
Expected: no Calendar event and no false booking-confirmation email. The independent
GitHub issue may still succeed, with its own execution result. Reconnect afterward.

### X04 — Partial failure: GitHub unavailable

First use provider fixtures. An optional live version disconnects GitHub only.
Expected: the Calendar reservation and Calendar-only reply can still succeed;
GitHub must report a failure/review state. Do not delete a valid Calendar event as
an assumed rollback. Check real state before retrying a failed/uncertain write.

## E. Local push / multiple repositories: P01–P04

These are for the user's device companion and native Git credentials, separate
from OAuth/cloud watchers. Use disposable branches with saved changes.

| Case | Operation | Expected |
| --- | --- | --- |
| P01 | Register and enable two real Git roots; make different saved edits; checkpoint once | Two independent remote commits on the chosen branches, correct files in each repo |
| P02 | Checkpoint again without changes | UP_TO_DATE; no empty duplicate commits |
| P03 | Leave an unsaved buffer in one repo | That repo is skipped; other valid repo is processed; unsaved content is never claimed as pushed |
| P04 | Make one repo use a missing remote, wrong local branch, or unavailable credentials | Clear per-repo failure; the valid repo is still processed; no force push or silent branch switch |

A sleeping/powered-off laptop cannot push local files. Cloud Gmail/Calendar/GitHub
watchers can continue only while their server runtime and authorizations remain available.

## Failure triage

| Observation | Investigate first |
| --- | --- |
| No detected email, no execution | Enabled/saved rule, sender/subject filters, new source message, monitor freshness/history cursor |
| MATCH but waiting | Approval policy and explicit approval; Draft Only intentionally does not write |
| FALLBACK_NOTIFICATION / EVENT_DETAILS_REQUIRED | Execution stage, saved timezone/duration and the actual source email |
| Calendar verification fails | Separate provider insert, event readback and execution evidence; check for an event before retrying |
| Calendar event exists but reply failed | Partial booking state; do not force a new booking |
| GitHub returns UNOBSERVED | Check the repository for an existing issue before retrying; an uncertain write is not proof nothing happened |
| Unrelated time is suggested | Email extraction, timezone, selected calendars, horizon, working hours and buffer |
| Meaning/classification is wrong | Capture authored text, expected classification, actual classification and model configuration; a fixture pass does not validate the live model |

Automated checks: `pytest -q` includes isolated Calendar/GitHub/Gmail orchestration,
policy/duplicate/partial-failure tests, and multi-topic reconciliation burst/lifecycle
tests. Provider calls and model outputs are controlled in those tests. Live OAuth,
the deployed model's semantic quality, and actual account permissions still require
the acceptance tests above.
