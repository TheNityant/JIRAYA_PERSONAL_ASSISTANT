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

## GitHub workflows in the app

GitHub appears in Connections with independent Connect/Disconnect controls. The GitHub
tab lets you select an accessible repository, inspect issues, pull requests and workflow
runs, create issues/branches, and request workflow dispatches. The repository picker
lists the most recently updated 100 repositories accessible to the connected account.
For other repositories, enter `owner/name` directly in an automation's repository field.

| Trigger | Action | Behavior |
| --- | --- | --- |
| GitHub issue changed | Notify me | Watch new or updated issues in one configured repository |
| GitHub pull request changed | Notify me | Watch PR state/content updates in one configured repository |
| GitHub workflow run changed | Notify me | Observe run status/conclusion changes in one configured repository |
| Gmail · New email | Create GitHub issue | Prepare an issue in a fixed user-selected repository, apply policy, create and read back |
| Gmail · New email | Create GitHub branch | Create a configured branch from a configured source branch, then verify its ref/SHA |

GitHub change triggers currently support notifications only. Set the repository in each
automation, then optionally filter title/body or supply a semantic condition such as
`A workflow run has completed with a failure`. The first poll baselines current activity;
it does not create notifications for existing history. Monitoring scans the most recent
100 activity items per repository and trigger on each cycle; brief intermediate states
or activity outside that window may not be observed. This is a local polling integration,
not an exhaustive webhook event archive. PR reviews/comments without a corresponding PR
update are not separate event triggers in this version.

Email-to-GitHub actions support Automatic, Ask before action, and Draft only. Repository
and branch names are fixed by the user's configuration, never taken from model output.
Click a proposal notification to inspect it and approve when required. All writes honor
`ENABLE_EXTERNAL_ACTIONS`. A durable database claim prevents duplicate automatic writes,
including when the API response is lost. An uncertain outcome stays unobserved and needs
manual inspection rather than sending the same issue creation request again.

Workflow dispatch is available as an explicit action in the GitHub tab. The selected
workflow must define `workflow_dispatch`; enter its filename or numeric ID, a ref, and
any supported inputs. A dispatch acknowledgement means the run was requested, not that
the run succeeded. Inspect workflow runs or configure a run-change notification to observe
the actual result. Workflow dispatch is not currently an automated Gmail action.

```env
GITHUB_BACKGROUND_MONITOR_ENABLED=true
GITHUB_BACKGROUND_POLL_SECONDS=60
```

Inspect `GET /github/status` for monitor health, or use `POST /runtime/github/poll`
for a manual poll. A repository-access failure is reported without stopping other
configured repositories. GitHub OAuth setup and the callback URL are listed above.

## Frontend sign-in and connections

The application now opens with account creation/sign-in, then a first-run connection
screen. Users never enter OAuth client IDs/secrets, provider tokens or backend settings.
Connect fetches a server-generated authorization URL and opens the provider's consent
page. The OAuth callback stores encrypted provider credentials under the signed-in
Jiraya account, then returns to the workspace. Missing application setup is shown as
Unavailable; failed starts remain inside the app instead of navigating to raw JSON.

The **application owner** still registers Jiraya with Google/GitHub once, configuring the
app-wide OAuth client credentials, public callback URLs and token-encryption key in the
backend host's secrets/settings. Those values identify Jiraya, not an individual end user.
They cannot be generated by a user clicking Connect and are never sent to the browser.

Accounts use email/password registration and sign-in, opaque HttpOnly cookie sessions,
server-side session revocation, password hashing and request-origin checks. OAuth
connections are separate from Jiraya sign-in. Password recovery and email verification
are not implemented in this version; add those before unrestricted public registration,
or set `REGISTRATION_ENABLED=false` after provisioning the intended test accounts.

All account-owned automation definitions, executions, events, actions, obligations,
notifications and external connections are isolated by the session identity. Background
polling iterates connected accounts with the same isolation. Signing out ends the browser
session; configured automations continue running in the background.

### Deployment configuration (owner only)

- Set `APP_ENV=production` and keep `AUTH_REQUIRED=true`.
- Set `FRONTEND_BASE_URL`, `FRONTEND_ORIGINS`, and frontend
  `NEXT_PUBLIC_JIRAYA_API_URL` to the actual deployed URLs.
- Configure the public callback URLs on each provider and in the corresponding backend
  `*_OAUTH_REDIRECT_URI` settings.
- Use HTTPS. Production cookies are Secure and HttpOnly. Prefer frontend/backend domains
  on the same site. For truly cross-site hosting, `SESSION_COOKIE_SAMESITE=none` also forces
  Secure cookies; browser third-party-cookie policies can still require same-site domains.
- Keep `TOKEN_ENCRYPTION_KEY` stable and private in host secrets.
- Configure `GMAIL_PUBSUB_WEBHOOK_TOKEN` if using the Gmail push endpoint. In authenticated
  mode, that endpoint is disabled when its shared secret is absent.

For default local settings, the frontend normalizes `localhost:3000` to
`127.0.0.1:3000` so browser sessions and the existing `127.0.0.1:8000` callbacks share a
site. Existing Google/GitHub callback URLs therefore continue to work locally.

### Existing local workspace

The additive database upgrade adds ownership columns without deleting existing records.
Existing single-user records remain assigned to the configured `DEV_USER_ID`; registering
a new account does not claim or expose that legacy workspace. Local-only legacy access
can be retained with `AUTH_REQUIRED=false` in development. Production rejects that mode.
The first registered account receives no special access to other users or legacy data.
