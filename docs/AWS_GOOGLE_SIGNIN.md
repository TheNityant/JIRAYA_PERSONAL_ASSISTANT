# Operator-assisted Google-account attendance on AWS (pilot)

This adds a manual Google browser sign-in to the existing worker. It does not convert OAuth tokens into cookies or bypass Google's browser/login checks. Real Google login, two-factor authentication and a live meeting must still be tested by the operator. This is an operator-assisted pilot, not a self-service multi-user login portal. A successful login does not guarantee admission to every meeting.

## Setup

1. Pause cloud attendance in Jiraya. On AWS, stop the worker before opening its profile:

```bash
cd ~/JIRAYA_PERSONAL_ASSISTANT
git pull --ff-only origin main
sudo docker compose -f deploy/meet/compose.yaml stop meet-worker
nano .env.meet
```

Set `MEET_CLOUD_IDENTITY_MODE=google_profile`. Leave existing keys, database, OAuth callbacks and encryption settings unchanged. Save the file. Optionally set `MEET_CLOUD_IDENTITY_MODE=google_profile` on the Render API too so the frontend describes the selected identity mode correctly; this does not move the browser to Render.

2. Build the new image (one-time desktop package download):

```bash
sudo docker compose -f deploy/meet/compose.yaml build meet-worker
```

3. Start the temporary sign-in desktop. Replace the email with the **Jiraya login email** for the account being configured. Its connected Calendar determines which Google account is required:

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

6. After the setup prints `Google account verified`, restart attendance:

```bash
sudo docker compose -f deploy/meet/compose.yaml up -d meet-worker
```

Close the SSH tunnel, re-enable cloud attendance in Jiraya and test a fresh Calendar meeting 5–10 minutes ahead. Each attempt verifies the browser's Google account again. A missing, expired or mismatched session stops instead of joining as a guest. Calendar link `authuser` account indices are removed before opening the canonical room on AWS.

## Storage and removal

Profiles are keyed by a hash of Jiraya user ID plus connected Calendar identity. They live in the named `google-profiles` Docker volume, separate from temporary evidence. Cookies are sensitive login material; they are not encrypted with Jiraya's database key. The host operator has privileged access. Do not export or share the volume. Calendar disconnection stops future attendance but does not erase these files.

To remove one profile, pause attendance, stop the worker and have the operator delete only the directory returned by `profile_path` for that exact Jiraya/Calendar identity. Revoke the corresponding browser session in Google Account security settings. Do not delete the shared volume to remove a single user's profile. Setup must be repeated if Google expires the login or the Calendar account changes.

Normal guest mode remains available with `MEET_CLOUD_IDENTITY_MODE=guest`; it does not use Google identity. Do not claim guest mode satisfies account-based attendance.
