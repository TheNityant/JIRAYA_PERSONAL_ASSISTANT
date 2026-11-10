# Frontend Google browser connection

The Google-account attendance worker can now accept an initial sign-in or reconnection from the Jiraya frontend. Users do not need SSH access to AWS. This controls a real remote browser; it is separate from Google OAuth consent. Google may still refuse the browser or invalidate its session, and host admission remains a separate requirement.

## Deploy

1. Deploy the current `main` to the Render API and Vercel frontend. The API adds the browser-setup/input tables during its existing database initialization. The API and AWS worker must use the same database and existing `TOKEN_ENCRYPTION_KEY`.
2. Pause attendance before rebuilding the single AWS worker:

```bash
cd ~/JIRAYA_PERSONAL_ASSISTANT
git pull --ff-only origin main
nano .env.meet
```

Set these two values (keep the existing profile root, keys and other settings):

```dotenv
MEET_CLOUD_IDENTITY_MODE=google_profile
MEET_CLOUD_BROWSER_PORTAL_ENABLED=true
```

```bash
sudo docker compose -f deploy/meet/compose.yaml up -d --build meet-worker
```

Keep one worker replica on the initial instance. Dockerfile.meet already installs Xvfb; source-only updates should reuse dependency/browser layers. Keep the named profile volume. Do not use `down -v`. No AWS public port, DNS record, new OAuth redirect URI, or frontend AWS URL is required. Do not open VNC ports. The worker advertises this portal capability through the shared heartbeat; until that updated worker is online, the frontend says it is unavailable.

## User flow

1. Sign into Jiraya and connect Google Calendar.
2. Open Meet in cloud mode. If attendance is enabled, pause it and wait for the active call to leave.
3. Read and accept the remote-browser disclosure, then select **Open Google meeting browser**.
4. Complete Google sign-in and two-factor checks in the remote image. The required Google email is shown above it. Click a Google field and type, paste, or use the masked mobile text helper. Enter and Tab work; Escape releases local keyboard focus. No Google password is entered automatically.
5. Once the Google account chip matches Calendar, the view disappears and the server closes the setup browser. It opens a fresh headless attendance browser against that same profile to verify persistence. **CONNECTED is reported only after that second check succeeds.** The setup itself never joins a meeting or calls AssemblyAI.
6. Enable cloud attendance using its separate transcription consent. Future meetings reuse the saved profile. Reconnect only when the session is missing or invalidated; a permanent Google session cannot be guaranteed.

Only one setup can use the initial meeting server at a time. Setup waits while the worker finishes a current meeting, and the entire connection expires after ten minutes, including queue time. If busy for longer, try when the worker is idle. Pausing attendance is not the same as deleting notes or removing the saved browser profile.

## Data and controls

- The frontend connects through its existing authenticated Vercel proxy and Render API. No public remote-desktop endpoint is exposed on AWS.
- Setup is bound to both the Jiraya user and the exact current Jiraya login session, plus the connected Calendar identity. Another user or another login session cannot read or control it.
- Screens and keyboard commands, potentially including login credentials, pass through the application infrastructure. They are encrypted with the application key while temporarily queued in the database. The host operator has privileged access. This is not a claim that the server cannot access credentials; use the feature only if the operator is trusted.
- Commands are ordered, bounded and removed before dispatch. Arbitrary JavaScript, arbitrary navigation URLs, browser address-bar shortcuts, file uploads and downloads are not offered. Main-page navigation is restricted to Google sign-in/Meet landing hosts; joining rooms is disabled during setup.
- Frame responses are private/no-store. Temporary screen and input payloads are logically erased on cancellation/completion/expiry. The running worker also cleans expired or lost sessions through its heartbeat. Provider backups and browser-profile storage have separate retention; this is not secure deletion of backups.
- Closing the view/unmounting the workspace requests cancellation. No view polling for 45 seconds, revoked/expired Jiraya login, Calendar disconnection/change, the ten-minute deadline, or worker shutdown also ends it. A lost worker lease is cleaned after 90 seconds. Cancellation cannot be overwritten by a late success result.
- Persistent Google cookies remain in the account-specific AWS profile volume. Closing the temporary view or signing out of Jiraya does not erase them. Removing that saved browser session remains an operator/Google Account security action. Enabled attendance can continue after ordinary Jiraya logout once setup has finished.
- Hardware security keys/passkeys tied to the user's local browser may not work in the remote browser. Use a Google-supported method offered on its screen. Do not automate passwords, bypass challenges, or suppress Google protections.

## Verification

`tests/test_meet_browser_portal.py` covers authorization, login-session isolation, CSRF, consent, capacity, encrypted ordered/idempotent inputs, cancellation races, expiry, logout, Calendar changes and persistence verification. With `JIRAYA_CHROMIUM_CONFIG` configured and frontend dependencies installed, it also launches the real frontend/proxy/API/DB/Chromium relay against controlled Google pages and verifies saved-profile reuse in two attendance sessions plus summary generation. Google, display startup, speech recognition and inference are explicit fixtures there: it is not a live Google/AWS/AssemblyAI acceptance test.

For live acceptance after deployment: use a consenting test account to complete frontend Google sign-in, confirm CONNECTED, schedule a new Calendar meeting 5–10 minutes ahead, admit the assistant, verify microphone/camera remain off, speak a short test statement and an explicit leave command, and inspect the captured transcript and summary. Repeat another meeting without reconnecting. Test cancellation and logout separately. No automated test in this repository proves that Google will admit the deployed AWS browser.
