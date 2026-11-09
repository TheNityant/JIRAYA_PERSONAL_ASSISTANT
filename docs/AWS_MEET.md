# AWS Lightsail Meet worker pilot

Keep the existing Render API, Vercel frontend, Google OAuth callbacks and hosted
PostgreSQL database. AWS runs only `Dockerfile.meet`. No GPU, new database,
public worker HTTP endpoint, load balancer or NAT gateway is required.

## 1. Create the instance in your AWS console

1. Check Billing → Credits for eligibility and expiry. Credits are not permanent
   free hosting. Set a monthly cost alert (alerts are not a spending cap).
2. In Amazon Lightsail choose Create instance. Pick the region closest to your
   existing database, Linux/Unix → OS Only → Ubuntu 24.04 LTS, **x86_64/amd64**.
3. Start with the public IPv4 2 GB Linux plan (listed at $12/month when prepared).
   Name it `jiraya-meet-worker`. This is a one-meeting pilot, not a capacity guarantee.
   The container is limited to 1536 MB to leave host headroom. Upgrade if measured
   memory/CPU or CPU burst capacity proves insufficient. Build memory also counts.
4. Attach a Lightsail static IPv4 address so the database allowlist stays valid.
   Keep inbound access limited to SSH; no application ports need opening. Restrict
   SSH to your IP and the Lightsail browser SSH option as supported by the console.
5. Connect using Lightsail's browser SSH terminal. No AWS access key needs to be
   pasted into Jiraya or this repository.

A stopped Lightsail instance still incurs instance charges. Delete unneeded
instances when finished, after retaining needed configuration. Snapshots,
unattached addresses and other extras may have separate charges. AssemblyAI and
Nebius usage are billed separately. Check prices and account credit terms before creation.

## 2. Prepare the host

In the browser SSH terminal:

```bash
git clone https://github.com/TheNityant/JIRAYA_PERSONAL_ASSISTANT.git
cd JIRAYA_PERSONAL_ASSISTANT
sudo bash deploy/meet/install-docker-ubuntu.sh
cp deploy/meet/worker.env.example .env.meet
chmod 600 .env.meet
nano .env.meet
```

The installer supports a fresh Ubuntu 24.04 amd64 host and installs Docker from
its official apt repository. On a host with existing Docker packages, follow the
vendor migration instructions rather than removing software blindly.

Fill `.env.meet` directly on the host. Do not paste secrets into chat, commit them,
put them in a Lightsail launch script, or include them in screenshots. Values are
unquoted plain text; Compose uses raw env-file parsing to preserve `$` characters.
Copy the working Nebius model and Google settings from Render. Keep
`TOKEN_ENCRYPTION_KEY` identical to Render's existing key.

Use the **same production database** as Render. AWS cannot use a Render private
hostname. Use the external connection URL with TLS (`sslmode=require`), and allow
only this instance's static IPv4 `/32` in addition to your existing necessary rules.
Keep URL-encoded database credentials intact. Jiraya uses SQLAlchemy's
`postgresql+psycopg://` scheme; replace only the original `postgres://` or
`postgresql://` prefix if needed, preserving host/path/query. If the URL already has
query parameters, append `&sslmode=require`, not another `?`.

The worker authenticates to Google with encrypted refresh tokens in the shared
database. Users do not need new OAuth clients or callback URLs for AWS.

## 3. Build and run one worker

After replacing every placeholder in `.env.meet`:

```bash
sudo docker compose -f deploy/meet/compose.yaml config --quiet
sudo docker compose -f deploy/meet/compose.yaml build
sudo docker compose -f deploy/meet/compose.yaml up -d
sudo docker compose -f deploy/meet/compose.yaml ps
sudo docker compose -f deploy/meet/compose.yaml logs --tail=100 meet-worker
sudo docker stats --no-stream
```

`config --quiet` validates without printing resolved secrets. Do not share output
from plain `docker compose config` or `docker inspect`, which can expose env values.
Compose restarts the worker after a process exit and Docker starts on host boot.
No raw browser profiles or audio are mounted on the host. Docker logs rotate.
Pausing Compose does not stop AWS instance billing.

For a database-heartbeat check (not a live meeting acceptance test):

```bash
sudo docker compose -f deploy/meet/compose.yaml exec -T meet-worker python - <<'PY'
from datetime import UTC, datetime
from app.db import SessionLocal
from app.services.runtime_state_store import RuntimeStateStore
with SessionLocal() as db:
    state = RuntimeStateStore(db).get('cloud-meet-worker') or {}
try:
    age = (datetime.now(UTC) - datetime.fromisoformat(state['at'])).total_seconds()
except (KeyError, ValueError, TypeError):
    raise SystemExit('No valid worker heartbeat')
print('Worker heartbeat fresh' if 0 <= age < 90 else 'Worker heartbeat stale')
raise SystemExit(0 if 0 <= age < 90 else 1)
PY
```

The first heartbeat may take a few seconds after startup. This is shared liveness
for a single worker; never treat it as proof of browser admission, audio capture,
provider credentials or summary quality. Do not run multiple workers during this pilot.

## 4. Enable the API and frontend only after worker readiness

On the existing Render API set the same AssemblyAI/model/encryption/Google settings
and `MEET_CLOUD_ENABLED=true`, `ENABLE_EXTERNAL_ACTIONS=true`; save and redeploy.
Keep Render's normal `Dockerfile` and start command. Keep the frontend API URL and
existing OAuth redirects unchanged. The AWS worker uses outbound APIs and the shared
database; the frontend must never point its normal API base to the Lightsail IP.

The current main frontend was deliberately switched to local-only navigation.
**Launching the worker alone does not restore cloud controls or enable attendance.**
Restore an explicit cloud setup entry after verifying worker readiness. Then the
user must review transcription consent and enable cloud attendance from that UI.
Pause local attendance on every paired computer to avoid duplicate participants.
No users are automatically opted in just by launching a worker.

Run every live acceptance check in [CLOUD_MEET.md](CLOUD_MEET.md), especially guest
admission, mic/camera off, real audio, explicit leave commands, encrypted notes and
summary coverage. A browser running on AWS can still be denied admission or blocked
by Google guest policies. Do not promise an unattended production service until tested.
No per-user daily or global spending cap has been implemented; keep the pilot private.

## 5. Stop and update

Outside active meetings, update with:

```bash
git pull --ff-only origin main
sudo docker compose -f deploy/meet/compose.yaml up -d --build
```

Updating/restarting an active worker interrupts attendance. Lost sessions are not
silently replayed. Allow current calls to finish first.

To shut down attendance, pause it in the frontend and stop the worker:

```bash
sudo docker compose -f deploy/meet/compose.yaml down
```

Keep `.env.meet` private. Delete the Lightsail instance and unused paid resources
through AWS when no longer needed; stopping the container or instance does not
eliminate the instance charge.

## Official references

- https://aws.amazon.com/lightsail/pricing/
- https://docs.aws.amazon.com/lightsail/latest/userguide/getting-started-with-amazon-lightsail.html
- https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-create-static-ip.html
- https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-how-to-connect-to-your-instance-virtual-private-server.html
- https://docs.docker.com/engine/install/ubuntu/
- https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/
- https://render.com/docs/postgresql-creating-connecting
