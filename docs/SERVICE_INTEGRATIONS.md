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

## Next integration layer

These service adapters intentionally separate deterministic provider access from the automation engine. The next layer is to add Calendar and GitHub trigger/action node types to the generic automation runtime and then add polling/webhook event sources for Calendar changes and GitHub repository/workflow events.
