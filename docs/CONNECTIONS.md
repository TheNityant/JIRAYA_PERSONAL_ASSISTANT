# Jiraya Connections and Automatic Gmail Runtime

Jiraya uses product-style OAuth connections. End users connect Gmail from the app; they do not create their own Google Cloud projects.

## Connection architecture

```text
Jiraya user
  -> Connect Gmail
  -> Google OAuth consent
  -> Jiraya callback
  -> encrypted OAuth credentials in PostgreSQL
  -> Gmail runtime / matcher / executor / verifier / reconciliator
```

`token.json` remains a legacy development fallback only while Web OAuth is not configured. Once `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are set, the database-backed connection is authoritative.

## One-time Google Cloud setup

Use the Jiraya Google Cloud project.

1. Keep the Gmail API enabled.
2. Create an OAuth client of type **Web application**.
3. Add the local redirect URI exactly:

   `http://127.0.0.1:8000/connections/google/callback`

4. Keep your Gmail account in OAuth Test users while the consent screen is in Testing.
5. Put the Web client ID and secret in local `.env`. Never commit them.

## Local environment

Generate an encryption key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add:

```text
TOKEN_ENCRYPTION_KEY=<generated Fernet key>
GOOGLE_OAUTH_CLIENT_ID=<web client id>
GOOGLE_OAUTH_CLIENT_SECRET=<web client secret>
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/connections/google/callback
FRONTEND_BASE_URL=http://localhost:3000

GMAIL_BACKGROUND_MONITOR_ENABLED=true
GMAIL_BACKGROUND_POLL_SECONDS=15
```

Do not paste secrets into chat, logs, screenshots, commits, or issues.

## Automatic behavior in local development

The backend runs a Gmail history monitor automatically. It does not repeatedly search the entire inbox; it stores the Gmail `historyId` cursor in PostgreSQL and consumes only mailbox changes after that cursor.

Because Jiraya watches Gmail history rather than only `INBOX`, it also observes the user's own `SENT` messages. That is necessary for obligation resolution, for example:

```text
Professor: submit assignment by Monday
  -> Jiraya auto-replies
  -> obligation OPEN

User later sends: submitted
  -> Gmail history event
  -> reconciliator
  -> obligation RESOLVED_BY_USER

Professor later says: file missing / more work required
  -> reconciliator
  -> new/reopened obligation
  -> ACTION_REQUIRED notification
  -> no blind repeat of the old auto-reply
```

The manual **Debug · Run latest** button remains only for development/testing. Normal enabled automations are consumed by the background runtime.

## Context-aware automatic actions

A semantic match establishes relevance, not unlimited authorization to send the same reply.

For an automation with `Approval policy = Automatic`, Jiraya runs an action-safety router:

- `AUTO_ACT`: routine situation clearly covered by the configured action.
- `REQUIRE_APPROVAL`: related situation, but changed details or uncertainty require review.
- `NOTIFY_ONLY`: complaint, failure, missing/incorrect work, new unsupported requirement, sensitive change, or unsafe ambiguity.

This prevents a broad condition such as `emails about assignment submission` from sending the same acknowledgement when a professor later reports that the assignment was missing.

## Notifications and obligations

Persistent notifications are stored in PostgreSQL and surfaced in the Notification Center as:

- `ACTION_REQUIRED`
- `AUTOMATION_EXECUTED`
- `INFORMATION`
- `STATE_CHANGED`

The Obligations screen shows durable work in OPEN and resolved/closed states.

## Real sending safety gate

The application still has a global development safety switch:

```text
ENABLE_EXTERNAL_ACTIONS=false
```

With this value, Jiraya can match, route, draft, create notifications, and maintain state, but outbound sends are blocked. Set it to `true` only when intentionally testing live automatic sending, then restart FastAPI.

## Optional production Gmail push using Pub/Sub

Local polling is enough to test the complete product flow. For a deployed backend, Gmail can wake Jiraya immediately through `users.watch` and Google Cloud Pub/Sub.

1. Create a Pub/Sub topic in the same Google developer project used for the Gmail watch, for example:

   `projects/<PROJECT_ID>/topics/jiraya-gmail`

2. Grant Pub/Sub Publisher permission on that topic to:

   `gmail-api-push@system.gserviceaccount.com`

3. Create a Pub/Sub push subscription whose endpoint is your public Jiraya backend:

   `https://<PUBLIC-JIRAYA-HOST>/webhooks/google/gmail?token=<random-shared-token>`

4. Set:

```text
GMAIL_PUBSUB_TOPIC_NAME=projects/<PROJECT_ID>/topics/jiraya-gmail
GMAIL_PUBSUB_WEBHOOK_TOKEN=<random-shared-token>
GMAIL_WATCH_AUTO_REGISTER=true
```

5. Reconnect Gmail, or call `POST /runtime/gmail/watch/register` once.

The Gmail watch response supplies a mailbox history cursor and expiration. Jiraya stores the cursor and consumes `history.list` changes through the same runtime used by the local monitor.

## Test sequence

1. `docker compose up -d db`
2. `python -m pytest`
3. Start FastAPI and wait a few seconds so the Gmail history cursor is initialized.
4. Start the Next.js frontend.
5. Confirm the sidebar says `Gmail monitoring · 15s` (or your configured interval).
6. Configure an enabled automation and set `Approval policy = Automatic`.
7. Keep `ENABLE_EXTERNAL_ACTIONS=false` for the first automatic test: send a matching email and confirm a blocked/action-required notification appears without pressing a Run button.
8. Set `ENABLE_EXTERNAL_ACTIONS=true`, restart FastAPI, then send a fresh matching email. Jiraya should route it, send only if safe, verify the send, create an `AUTOMATION_EXECUTED` notification, and create an obligation when the source message contains durable work.
9. Send a manual reply that satisfies the obligation. It should become resolved automatically.
10. Send a later incoming message that invalidates or changes the outcome. The obligation should reopen/create a new follow-up and Jiraya should notify rather than blindly repeating the prior automatic reply.
