# Automations while the user's laptop is closed

Gmail, Google Calendar and remote GitHub watchers run on the server. Browser
requests are not their scheduler. The monitors enumerate connected users from
PostgreSQL and restore their saved history cursors and watcher snapshots. They do
not require an active `AuthSession`, an open browser tab, or the user's laptop.
Signing out ends browser access; it does not pause saved automations. To stop
automation, disable its rule or disconnect the relevant provider.

This does not make every connected account perform actions: the user must save an
enabled automation with a matching trigger. `REQUIRED` and `DRAFT_ONLY` still wait
for user input. `AUTO` permits execution only when the existing safety checks pass,
and writes also require `ENABLE_EXTERNAL_ACTIONS=true`.

## Start with one always-on backend

This is the smallest deployment change for the current Render/Vercel setup. Use
an always-on Render web service and a durable PostgreSQL database. Render's free
web services sleep after 15 minutes without inbound traffic, stopping in-process
polling. Free Render Postgres expires after 30 days. Choose compute/storage with
the lifetime and backups needed for your deployment; code cannot remove those
hosting limits. No infrastructure or paid plan is created by this change.

On the backend, keep the existing production OAuth, frontend proxy, database,
authentication, encryption and model configuration, and set:

```dotenv
AUTOMATION_RUNTIME_MODE=embedded
WEB_CONCURRENCY=1
GMAIL_BACKGROUND_MONITOR_ENABLED=true
CALENDAR_BACKGROUND_MONITOR_ENABLED=true
GITHUB_BACKGROUND_MONITOR_ENABLED=true
ENABLE_EXTERNAL_ACTIONS=true
```

Use one backend instance with one process. Deploy `phase2e-generic-execution`.
The Dockerfile starts the API and its embedded monitors. A separate worker is not
required for this setup, so do not pay for a second service just to close a laptop.

## Optional separate automation worker

Use this when API deployments or API idling should not control monitoring. The
worker remains an always-on service; an idle free API does not make the worker free.

1. Deploy the same branch/image as a Render Background Worker, one instance.
2. Set `AUTOMATION_RUNTIME_MODE=external` on **both** API and worker. Keep the
   three monitor flags enabled on both so the API can report the expected state.
   The API does not launch monitors in external mode.
3. Give the worker the **same** `DATABASE_URL`, stable `TOKEN_ENCRYPTION_KEY`,
   OAuth client credentials, model configuration, `APP_ENV=production`,
   `AUTH_REQUIRED=true` and `ENABLE_EXTERNAL_ACTIONS` value as the API. It reads
   provider tokens from the database; users never paste tokens into worker settings.
4. Override the worker's Docker command with:

   ```sh
   python -m app.runtime.worker
   ```

   The installed `jiraya-worker` command is equivalent. A worker has no HTTP port
   and no health-check URL; do not deploy it as a web service expecting a listener.
5. Stop the embedded monitors before starting the worker. Do not run multiple
   workers or leave a second embedded API instance connected to the same database.
   Confirm the worker's startup log and recent per-account status before relying
   on it. The CLI refuses to start in embedded mode or with all monitors disabled.

The worker uses the same monitor/executor code as the API. SIGTERM stops taking new
accounts and lets an active poll finish. Render can still kill it when the platform
shutdown deadline expires. Persisted cursors resume on restart, and existing
execution records/deduplication remain in effect. This is a single-worker polling
runtime, not a distributed job queue or an exactly-once guarantee for external
side effects. Do not horizontally scale it until distributed claims are implemented.

## Check it without keeping the app open

While signed in, open:

```text
https://jiraya-personal-assistant.vercel.app/api/jiraya/runtime/status
```

This authenticated endpoint reports only the current user's three monitors:
`MONITORING`, `IDLE` (no watcher rules), `MANUAL_ONLY`, `STARTING`, `STALE`, `ERROR`, `DISABLED`,
or `DISCONNECTED`. A recent successful check means operational, not a promise of
future uptime. A poll older than max(180 seconds, three polling intervals) is stale;
a slow active poll can also exceed that threshold. The existing service cards and
Gmail runtime status use the same freshness calculation. `/health` alone verifies
the HTTP server, not background automation.

For a real acceptance test:

1. Resolve OAuth errors, connect Gmail and Calendar, and save an enabled **AUTO**
   create-event automation. Set Asia/Kolkata and the desired default duration.
   First verify one ordinary event from a new email with the app open.
2. Close every Jiraya browser tab and shut down the laptop. After at least 20
   minutes, use a different account/device to send a new matching email containing
   an unambiguous **future date and start time** and a unique subject.
3. Check Google Calendar directly on a phone. It should contain exactly one event
   after a normal poll/execution interval. Reopen Jiraya and inspect the saved
   execution, timestamps and monitor status. Do not press Debug or Poll to make
   this test pass.
4. For GitHub, configure an explicit repository watcher, let its initial baseline
   complete, then close the app and change a watched issue/PR from another device.
   Reopen Jiraya and check its saved notification/execution. Initial baselining
   intentionally does not replay old repository activity.
5. For a separate-worker deployment, restart the **API only** during the test and
   verify worker polling continues. Restart the worker separately and confirm it
   resumes saved cursors without replaying completed executions.

Notifications generated while offline are stored for the next app visit. This
change does not add phone push notifications or implement Google Meet attendance.

## What still requires a running device

The workspace checkpoint companion reads local `.git` repositories and files.
A powered-off or sleeping laptop cannot push unsaved local changes. That feature
needs the local companion, repository, network and Git credentials available at
execution time. A cloud VM can process repositories cloned onto that VM, but cannot
read fresh changes on a closed laptop. Unsaved IDE buffers are not disk changes.

## Authorization is renewable, not permanent

Google access tokens refresh server-side using encrypted saved refresh tokens;
the existing GitHub integration refreshes expiring credentials when supported.
Browser session expiry is separate from provider consent. Revoked consent,
missing scopes, invalid refresh tokens, changed client credentials or account
policies still require the user to reconnect. External Google OAuth apps in
Testing generally have refresh tokens expire after seven days when requesting
Gmail/Calendar scopes. Configure publishing/verification as Google requires for
the intended audience; do not promise permanent unattended authorization from a
test OAuth client.

References:

- https://render.com/docs/free
- https://render.com/docs/background-workers
- https://developers.google.com/identity/protocols/oauth2#expiration
