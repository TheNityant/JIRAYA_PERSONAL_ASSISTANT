# Operator-assisted Google-account attendance on AWS (fallback)

The frontend setup is documented in [MEET_BROWSER_PORTAL.md](MEET_BROWSER_PORTAL.md). Use this SSH method only as the operator fallback.

This adds a manual Google browser sign-in to the existing worker. It does not convert OAuth tokens into cookies or bypass Google's browser/login checks. Real Google login, two-factor authentication and a live meeting must still be tested by the operator. This is an operator-assisted pilot, not a self-service multi-user login portal. A successful login does not guarantee admission to every meeting.

## Setup

1. Pause cloud attendance in Jiraya. On AWS, stop the worker before opening its profile:

```bash
cd ~/JIRAYA_PERSONAL_ASSISTANT
git pull --ff-only origin main
sudo docker compose -f deploy/meet/compose.yaml stop meet-worker
nano .env.meet
```

Set `MEET_CLOUD_IDENTITY_MODE=google_profile`. Leave existing keys, database, OAuth callbacks and encryption settings unchanged. Save the file. The worker publishes its identity mode through the shared heartbeat, so the frontend reads the actual worker mode even when the API environment still defaults to guest. Keep the API and worker configuration consistent where possible; no frontend AWS URL is needed.

2. Build the image (desktop packages are cached after the first build):

```bash
sudo docker compose -f deploy/meet/compose.yaml build meet-worker
```

3. If you have already signed in on this AWS volume, first check the saved session without opening an interactive desktop:

```bash
sudo docker compose -f deploy/meet/compose.yaml run --rm --no-deps meet-worker \
  python -m app.meet_cloud.auth --check --user-email 'YOUR_JIRAYA_LOGIN_EMAIL'
```

This opens a headless browser to verify the saved account. It does not join, listen, request a password or consume AssemblyAI transcription. On success skip to step 6. A timeout/network/runtime error is not proof that Google revoked the session; inspect that error before asking for another login. On `No saved browser profile` or `Google browser reconnection required`, complete the interactive setup below.

Start the temporary sign-in desktop. Replace the email with the **Jiraya login email** for the account being configured. Its connected Calendar determines which Google account is required:

```bash
sudo docker compose -f deploy/meet/compose.yaml run --rm --no-deps \
  -p 127.0.0.1:6080:6080 meet-worker \
  python -m app.meet_cloud.auth --user-email 'YOUR_JIRAYA_LOGIN_EMAIL'
```

Leave this terminal open. The setup times out after 10 minutes and closes the desktop. It does not join a meeting. Never run setup and attendance against the same profile at the same time.

4. On your own computer, open a second terminal and create an SSH tunnel using your existing Lightsail SSH key and public IP:

```bash
ssh -i "PATH_TO_YOUR_LIGHTSAIL_KEY.pem" -L 6080:127.0.0.1:6080 ubuntu@YOUR_AWS_PUBLIC_IP
```

Use the SSH key locally; do not upload it to Jiraya or share it. The browser-based Lightsail terminal alone does not create this tunnel.

5. In your computer's browser open `http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale`. Complete Google sign-in manually in the displayed AWS desktop, including any two-factor step. Use the same account connected to Calendar. After signing in, return to `https://meet.google.com/` in that desktop if needed. Setup verifies the Google account chip's email matches Calendar, then closes and saves the profile.

The desktop HTTP/VNC transport relies on the SSH tunnel. **Do not open ports 6080 or 5900 in the Lightsail firewall or change the Docker binding to 0.0.0.0.** They expose a privileged interactive desktop. Only the account owner and trusted server operator should use this setup. If Google refuses sign-in, stop and report that message; do not disable Google protections or automate password entry.

6. After setup prints `Google account verified`, or `--check` verifies the existing saved session, restart attendance:

```bash
sudo docker compose -f deploy/meet/compose.yaml up -d meet-worker
```

Close the SSH tunnel, re-enable cloud attendance in Jiraya and test a fresh Calendar meeting 5–10 minutes ahead. Each attempt verifies the browser's Google account again. A missing, expired or mismatched session stops instead of joining as a guest. Calendar link `authuser` account indices are removed before opening the canonical room on AWS.

## Storage and removal

Profiles are keyed by a hash of Jiraya user ID plus connected Calendar identity. They live in the named `google-profiles` Docker volume, separate from temporary evidence. Cookies are sensitive login material; they are not encrypted with Jiraya's database key. The host operator has privileged access. Do not export or share the volume. Calendar disconnection stops future attendance but does not erase these files.

To remove one profile, pause attendance, stop the worker and have the operator delete only the directory returned by `profile_path` for that exact Jiraya/Calendar identity. Revoke the corresponding browser session in Google Account security settings. Do not delete the shared volume to remove a single user's profile. Setup must be repeated if Google expires the login or the Calendar account changes.

Normal guest mode remains available with `MEET_CLOUD_IDENTITY_MODE=guest`; it does not use Google identity. Do not claim guest mode satisfies account-based attendance.

## Reuse and readiness contract

This is **initial sign-in plus persistent session reuse**, not password entry for each meeting and not a promise of permanent Google authentication. The same hashed user/Calendar profile is used for every meeting. `docker compose build`, `up`, container recreation and normal worker restarts preserve the named volume. Do not use `docker compose down -v` or delete that volume during deployment. Moving to another AWS machine requires a fresh setup unless the sensitive profile storage is deliberately migrated by the trusted operator.

The API distinguishes browser `SETUP_REQUIRED`, `CONNECTED` (last verified) and `RECONNECT_REQUIRED`. Browser setup must be verified before enabling account-based attendance. A missing/invalid session detected during a join produces a deduplicated in-app notification and blocks later discovery attempts until setup/check verifies it again. It does not send email or push notifications. Verified reconnection retires that profile's stale alert. The enabled preference stays saved, so future eligible meetings can resume after reconnection. Already missed/failed meetings are not automatically replayed.

`CONNECTED` does not prove meeting admission. Host rejection, a network outage, a worker failure and a missing Google browser session are separate conditions. Every join verifies the browser's Google account again and never falls back to an anonymous name. A bounded account-chip wait avoids treating normal page hydration as an expired login.

The frontend portal now provides setup and reconnect controls when the worker enables `MEET_CLOUD_BROWSER_PORTAL_ENABLED=true`; see [MEET_BROWSER_PORTAL.md](MEET_BROWSER_PORTAL.md). This document retains the operator-assisted SSH fallback.

## Why OAuth/watch is separate

Google OAuth authorizes scoped API calls; it does not supply the user's Google password or a signed-in Meet website session. Google Workspace event subscriptions notify us about conference/participant/artifact changes; they do not join a call. The supported Meet REST API manages meeting spaces and accessible conference artifacts. The Meet Media API preview is closed to new signups and is scheduled to shut down on January 15, 2027, so it is not a new integration path for this app.

References: [OAuth web-server authorization](https://developers.google.com/identity/protocols/oauth2/web-server), [Meet REST capabilities](https://developers.google.com/workspace/meet/api/guides/overview), [Meet event subscriptions](https://developers.google.com/workspace/events/guides/events-meet), [Meet Media API status](https://developers.google.com/workspace/meet).

Do not collect Google passwords or turn OAuth tokens into browser cookies through undocumented token-exchange mechanisms. If Google refuses this browser's direct sign-in, stop and investigate the message. The application cannot guarantee that Google will accept an automated browser or that its session will never expire.
