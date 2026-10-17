# Jiraya service integrations

This document covers the non-Gmail integrations currently implemented on `phase2e-generic-execution`.

## Google Calendar

Jiraya keeps Calendar credentials in a separate `google_calendar` connection so Calendar can be tested independently from the Gmail connection.

### Google Cloud setup

1. Enable **Google Calendar API** in the same Google Cloud project used for Jiraya.
2. Add these OAuth scopes to the consent configuration when required by the project:
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/calendar.events.freebusy`
   - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
3. Add this redirect URI to the existing Web OAuth client:
   - `http://127.0.0.1:8000/connections/google/calendar/callback`
4. Keep the existing Google client ID and secret in `.env` only.

### Local `.env`

```env
GOOGLE_CALENDAR_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/connections/google/calendar/callback
```

The existing `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `TOKEN_ENCRYPTION_KEY` are reused.

### Connect and test

Open:

```text
http://127.0.0.1:8000/connections/google/calendar/start
```

Then inspect:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/calendar/status | Format-List
Invoke-RestMethod http://127.0.0.1:8000/calendar/events
```

Core API surface:

- `GET /calendar/status`
- `GET /calendar/events`
- `POST /calendar/freebusy`
- `POST /calendar/events`
- `PATCH /calendar/events/{event_id}`
- `DELETE /calendar/events/{event_id}`

Calendar writes respect `ENABLE_EXTERNAL_ACTIONS`.

## GitHub

The current hackathon implementation uses a GitHub OAuth App for fast single-user authorization. A GitHub App is the preferred long-term architecture when Jiraya needs fine-grained per-repository installations and webhook-driven production monitoring.

### GitHub OAuth App setup

Create an OAuth App in GitHub Developer Settings with:

- Homepage URL: `http://localhost:3000`
- Authorization callback URL: `http://127.0.0.1:8000/connections/github/callback`

Store the real credentials only in `.env`:

```env
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
GITHUB_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/connections/github/callback
GITHUB_OAUTH_SCOPES=repo workflow read:user
```

### Connect and test

Open:

```text
http://127.0.0.1:8000/connections/github/start
```

Then inspect:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/github/status | Format-List
Invoke-RestMethod http://127.0.0.1:8000/github/profile | Format-List
Invoke-RestMethod 'http://127.0.0.1:8000/github/repos?limit=10'
```

Core API surface:

- `GET /github/status`
- `GET /github/profile`
- `GET /github/repos`
- `GET /github/repos/{owner}/{repo}/workflow-runs`
- `GET /github/repos/{owner}/{repo}/issues`
- `POST /github/repos/{owner}/{repo}/issues`
- `POST /github/repos/{owner}/{repo}/branches`
- `POST /github/repos/{owner}/{repo}/workflow-dispatch`

GitHub writes respect `ENABLE_EXTERNAL_ACTIONS`.

## Calendar workflows in the app

Calendar now appears in Connections with its own Connect/Disconnect controls. The
Calendar tab lists upcoming primary-calendar events and the background sync state.
Calendar authorization remains separate from Gmail; connect it once after completing
the Google Cloud setup above.

| Trigger source | Action | Behavior |
| --- | --- | --- |
| Gmail · New email | Create calendar event | Match email, extract a dated proposal, apply approval policy, create and read back the event |
| Calendar · Event changed | Notify me | Incrementally sync primary-calendar changes and notify on matching events |

For email-to-event automations, put your timezone and any default duration into Personal
context (for example, `Use Asia/Kolkata. Default meeting duration: 30 minutes.`). Action
instructions define which event to create. A clear date, start and end are required;
missing or ambiguous scheduling information produces a notification rather than an
invented event. The proposal is visible in the execution panel, reachable by clicking
its notification.

- **Automatic** creates the event when the safety router allows the configured action.
- **Ask before action** saves a proposal with an Approve & Create event button.
- **Draft only** saves the proposal without creating an event.
- `ENABLE_EXTERNAL_ACTIONS=false` blocks all Calendar writes.
- Automated event creation targets the primary calendar and sends no invitations.
- A stable provider event ID prevents duplicate events during retries or forced runs.
- Success requires reading the event back and checking its identity, title and times.

For Calendar change notifications, choose Calendar as trigger source and Notify me as
action. The first poll establishes a baseline, so existing calendar history does not
flood notifications. Later new, updated and cancelled events are matched by event title,
description and optional semantic condition. Deleted events may contain only an ID and
cannot always match title/description filters. Sync expiration (HTTP 410) establishes a
fresh baseline. Calendar change triggers currently support notifications only.

Calendar monitoring runs independently of Gmail:

```env
CALENDAR_BACKGROUND_MONITOR_ENABLED=true
CALENDAR_BACKGROUND_POLL_SECONDS=30
```

Inspect `GET /calendar/status` for monitor health or use `POST /runtime/calendar/poll`
for a manual sync. Primary-calendar scope is fixed for this first integration.

GitHub service adapters remain available through their direct API endpoints. GitHub
trigger/action nodes and repository/workflow background monitoring are not yet wired
into the automation engine.
