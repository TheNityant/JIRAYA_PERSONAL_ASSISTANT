# Jiraya Personal Assistant

Jiraya is an evidence-aware Personal AI for the **Nebius × NVIDIA Global AI Hackathon 2026 — Personal AI** track.

Core product loop:

```text
Agent -> Verifier -> Event Ledger -> Reconciliator
```

The distinguishing mechanism is temporal outcome assurance: Jiraya records evidence that an action really succeeded, then later uses event history to distinguish an original execution failure from an outcome that was legitimately superseded or externally invalidated afterward.

## Frontend account and connection flow (Phase 2e)

Open the web app, create a Jiraya account or sign in, then choose **Connect Gmail**,
**Connect Calendar**, or **Connect GitHub**. The provider opens its authorization screen
and returns to your own workspace. End users do not edit environment files or supply
OAuth application credentials. The app owner configures the provider applications once.

See [service integrations and deployment](docs/SERVICE_INTEGRATIONS.md#frontend-sign-in-and-connections)
for account sessions, connection setup, production URLs and existing-workspace handling.
The sections below retain the original Phase 1 standalone CLI workflow for reference.

## Current implementation: Phase 1 Gmail vertical slice

```text
Unread Gmail message
        |
        v
Nemotron classification
        |
        v
Approved internship workflow?
        |
        +-- no --> NO_ACTION
        |
        v
Approved reply template
        |
        +-- dry run --> preview only
        |
        v
Gmail send
        |
        v
Deterministic Gmail verification
        |
        +--> VERIFIED_SUCCESS
        +--> EXECUTION_FAILED
        +--> UNOBSERVED
        |
        v
PostgreSQL Event Ledger
```

Real external actions are safety-gated. `dry_run=true` is the default, and a non-dry run is still blocked while `ENABLE_EXTERNAL_ACTIONS=false`.

## Setup

Use the Phase 1 branch:

```powershell
git fetch origin
git checkout phase1-gmail-agent
git pull origin phase1-gmail-agent
```

Activate your existing Python virtual environment, then install the updated dependencies:

```powershell
python -m pip install -e ".[dev]"
```

Keep PostgreSQL running:

```powershell
docker compose up -d db
```

## Gmail OAuth setup

In Google Cloud:

1. Create or select a project.
2. Enable **Gmail API**.
3. Configure the Google Auth Platform / OAuth consent screen.
4. Create an **OAuth 2.0 Client ID** of type **Desktop app**.
5. Download the client JSON and save it in the repository root as `credentials.json`.
6. If the OAuth app is in Testing mode, add the Gmail account you will connect as a test user.

Phase 1 requests only:

```text
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.send
```

`credentials.json` and `token.json` are gitignored and must never be committed.

Authorize locally:

```powershell
python -m app.integrations.gmail.auth
```

A browser window opens for Google authorization. A successful flow writes `token.json` locally. If Gmail scopes change later, delete `token.json` and authorize again.

## Run the API

```powershell
python -m uvicorn app.main:app --reload
```

Check:

```text
GET http://127.0.0.1:8000/gmail/status
```

Expected shape:

```json
{
  "credentials_present": true,
  "token_present": true,
  "connected": true,
  "email_address": "you@example.com"
}
```

## Safe first test

From another account, send the connected Gmail account a new unread test message about an internship. Do not use a real recruiter thread for the first run.

Inspect it:

```text
GET http://127.0.0.1:8000/gmail/messages/latest
```

Dry-run the workflow:

```powershell
$body = @{ dry_run = $true } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/workflows/internship-inquiry/run" `
  -ContentType "application/json" `
  -Body $body
```

A qualifying message should produce `decision: DRY_RUN` plus a proposed reply. Nothing is sent in dry-run mode.

After reviewing the preview, you can explicitly enable external actions in `.env`:

```env
ENABLE_EXTERNAL_ACTIONS=true
```

Restart Uvicorn, then run with `dry_run=false` against your test message. A successful end-to-end result should return:

```text
decision            : SENT
verification_status : VERIFIED_SUCCESS
```

The Verifier does not ask another LLM whether the action worked. It independently queries Gmail for the sent-message ID and checks source-of-record facts: `SENT` label, thread ID, and recipient.

Use the returned `correlation_id` to inspect the evidence chain:

```text
GET /events?correlation_id=<correlation_id>
```

Expected sequence:

```text
EMAIL_OBSERVED
EMAIL_CLASSIFIED
EMAIL_REPLY_PROPOSED
EMAIL_REPLY_SENT
EMAIL_REPLY_VERIFIED
```

## Tests

```powershell
python -m pytest
```

Phase 1 adds tests for structured email classification, deterministic Gmail verification, and the no-send dry-run safety policy. These tests do not require a live Gmail account.

## Phase 1 acceptance criteria

- Existing Phase 0 health and Nemotron checks still work
- `/gmail/status` reports `connected: true`
- `/gmail/messages/latest` reads the test message
- Dry run classifies the test email and proposes the approved reply
- Dry run never sends mail
- Real sending remains blocked while `ENABLE_EXTERNAL_ACTIONS=false`
- With explicit enablement, a test reply sends
- Verifier returns `VERIFIED_SUCCESS`
- Event Ledger contains the complete evidence chain
- `python -m pytest` passes

## Intentionally deferred

- Always-on Gmail Pub/Sub watcher
- Temporal reconciliation of later replies/bounces
- Calendar/GitHub connectors
- Free-form autonomous email replies
- LangSmith tracing

Those come after this vertical slice is reliable.
