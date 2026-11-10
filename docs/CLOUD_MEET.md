# Cloud Meet attendance and notes (opt-in beta)

This is a dedicated background browser service, separate from the FastAPI HTTP
service and the local companion. A guest named **Jiraya · AI meeting notes** joins
eligible Calendar events near their start. Users may close their laptop after
cloud attendance is enabled. The worker, database and external providers must stay
available. Guest admission is controlled by Google and the host; OAuth is not a
way to bypass sign-in or guest restrictions. No Google login cookies, passwords,
user browser profiles or CAPTCHA bypasses are uploaded or automated.

## Deploy

1. Keep the normal FastAPI Dockerfile on the existing Render API service.
2. Create a separate **Background Worker** from the same repository and `main`,
   choose Docker, and set its Dockerfile path to **Dockerfile.meet**. The image
   includes Chromium, PulseAudio, FFmpeg and the application. Its default command
   starts private audio infrastructure then `python -m app.meet_cloud.worker`.
3. Configure **both API and worker** with the same hosted `DATABASE_URL`,
   `TOKEN_ENCRYPTION_KEY`, Google OAuth client settings and Nebius settings. Use
   PostgreSQL in production. Keep the encryption key stable. Configure:

   ```env
   MEET_CLOUD_ENABLED=true
   ENABLE_EXTERNAL_ACTIONS=true
   ASSEMBLYAI_API_KEY=<operator key>
   ASSEMBLYAI_SPEECH_MODEL=universal-streaming-english
   MEET_CLOUD_MAX_MINUTES=120
   MEET_CLOUD_RETENTION_DAYS=7
   NEBIUS_API_KEY=<existing key>
   ```

   Never put provider keys in Vercel's public environment variables or the browser.
   The worker does not need the local companion or an HTTP port. Keep the existing
   Gmail/Calendar automation worker/API configuration; this worker handles only Meet.
   Its startup requires valid encryption and live-listening configuration. The API
   also checks model configuration before allowing users to enable cloud attendance.
4. Start with **one worker replica** and a paid allocation with sufficient memory
   for Chromium (budget roughly 2 GB and measure CPU/memory under a real call).
   One process attends one meeting at a time; additional worker replicas can claim
   separate sessions atomically. Overlapping meetings can be missed if all workers
   are busy. This is not a high-availability autoscaling meeting platform yet.
5. Open **Meet → Cloud attendance and summaries**. The UI shows worker readiness.
   Connect Calendar, review audio/transcription consent and explicitly enable.
   This stops local attendance on the currently paired computer when switching;
   separately pause any other paired devices before enabling cloud attendance.

The service image must be built and exercised on your deployment platform. This
repository's fixture tests do not prove that platform's Chromium/audio runtime,
Google guest policy, AssemblyAI key or host-admission behavior. Do not represent
cloud attendance as production-ready before the acceptance test passes.

## Google transcripts versus live speech

- Live leave detection needs live audio recognition. **AssemblyAI streams the call
  while admitted even if Google is preferred for the summary.** This setting does
  not remove live transcription costs. There is no raw audio recording on disk.
- The initial summary tries a generated Google transcript. When additional Google
  access is authorized, it waits up to five minutes for generation, checking every
  30 seconds, then falls back to live speech text. Without that permission it uses
  live speech immediately. Google artifacts may not be available immediately, at all, or to
  this account. Native transcription depends on the meeting's plan/settings and
  must have been generated. A guest bot's admission does not grant the connected
  Google user access to the host's artifacts.
- Enable **Google Meet API** in the Google Cloud project. The optional **Authorize
  Google transcript access** button uses the existing Calendar web OAuth callback
  with an additional `https://www.googleapis.com/auth/meetings.space.readonly`
  scope. Declare/justify this scope and obtain Google verification before public
  rollout. It is requested only by this explicit flow; normal Calendar consent
  does not change. No broad Drive read permission is required for transcript entries.
- A single ended conference record matching the event's room and time window must
  be found. Different/reused-room records are never arbitrarily selected. Entries
  and transcript pages are paginated. No synthetic transcript is created.
- After the scheduled end, **Retry summary / check Google transcript** rechecks
  Google and regenerates notes without rejoining or rerecording the meeting.
- The live transcript and summary are encrypted with `TOKEN_ENCRYPTION_KEY`.
  Model summaries are labeled with their transcript source and coverage. Empty
  speech does not produce an invented summary. Provider failures retain live text
  for an explicit retry. Live text is capped at 500,000 characters; this summary
  tier accepts up to 120,000 characters and reports unavailable rather than silently
  truncating larger transcripts. Google-preferred notes may include meeting speech outside
  the bot's attendance window when the authorized Google transcript covers it.

## Attendance and exits

- The bot only joins events returned from the user's connected primary Calendar,
  with one validated HTTPS Google Meet room, valid times, and no declined/cancelled/
  transparent/all-day state. There is no arbitrary URL execution endpoint.
- A fresh browser profile, audio sink and temporary evidence directory are used for
  each job and removed on normal completion. Browser environment excludes API keys.
  This is container isolation, not a certified hardened multi-tenant browser sandbox.
- The joiner requires camera/microphone OFF evidence before admission and checks it
  during attendance. It never unmutes, publishes video, clones a voice or replies.
- Host-ended/removed UI, a user stop request, pausing cloud attendance, worker
  shutdown or the scheduled end/duration cap ends attendance. Audio capture or
  transcription failure ends the attempt and marks partial coverage.
- Optional spoken exits recognize explicit commands like **Jiraya, please leave**,
  **the meeting is over**, or **everybody, you can leave now**. Bare “bye”, quoted
  phrases, hypothetical instructions and negations do not end the session.
  Mixed audio cannot establish host identity; **any participant** may issue an
  audible matching command. Do not claim authenticated host-role detection.
- Session claims are persisted before launching. A lost heartbeat becomes
  REVIEW_REQUIRED and is not automatically replayed, preventing duplicate bots
  after a crash. There is no automatic retry loop around Google admission failures.
- Retention cleanup runs in the worker, default seven days, including encrypted
  notes. Users may delete completed sessions immediately. Database backups and
  provider-side retention are separate. Deletion retains only an opaque deduplication
  hash until one day after the scheduled end so deleting notes cannot cause a rejoin. A stopped worker cannot perform cleanup.

## Acceptance test before enabling real users

1. Deploy the dedicated worker; confirm the cloud UI says configured/online.
2. Use test accounts and a host who consents to transcription. Create a fresh event
   5–10 minutes ahead with a real Meet URL; enable cloud attendance. Close Jiraya
   and turn off the user's computer. Keep a separate host device online.
3. Admit the named guest. Verify directly in Meet that mic/camera are off. Speak
   a small script with a decision and an assigned action. Say “bye Alex, we will
   continue” and confirm it stays. Say “Jiraya, please leave” and confirm it exits.
4. Confirm the session shows completed/partial truthfully and the live transcript
   and summary cover the spoken test. Test an empty/silent meeting too.
5. Repeat with native Google transcription enabled and the correct artifact-access
   account. Retry summary after the event ends; verify source changes to Google
   only when an accessible generated transcript exists. Test missing permission,
   no native transcript and delayed generation: AssemblyAI fallback must be explicit.
6. Test denied admission, guest-blocked meetings, stop while knocking, pause while
   active, expired/revoked Calendar access, worker shutdown, and lost heartbeat.
   Verify no unsolicited rejoin and no falsely reported successful attendance.
7. Sign into another Jiraya account: it must not see or alter the first account's
   sessions. Delete notes; verify removal. Observe live CPU, memory and provider
   usage before raising concurrency or offering this to other users.


## Diagnosing a missed join and Google identity

Use **Meet → Run Meet checks** before testing a fresh event. The read-only check reports API configuration, external actions, the worker heartbeat, Calendar access and event counts. No provider keys are returned and no meeting is joined. Automatic attempts are made from 30 seconds before start through two minutes after start. Enabling attendance after that window does not replay the meeting. Check the session reason for admission failures and sign-in requirements. An online heartbeat proves database contact, not browser admission or audio capture.

Cloud attendance deliberately uses an anonymous guest with a fresh browser profile. Connecting Calendar does not sign Chromium into Google. A guest display name cannot confer the user's Google identity or access to sign-in-only meetings. To attend as your actual Google account with the currently supported implementation, select **Use this computer instead**, pair the companion and manually sign into its separate Google browser profile with the intended Calendar account. Google supplies that signed-in profile's participant name. Do not give Jiraya your Google password or upload browser cookies. A per-user, remotely authenticated cloud browser is not implemented by this deployment.

Newer Google `/v3/signin` and AccountChooser routes are now recognised as sign-in walls, so they no longer appear as an unrelated unexpected host. This detection fix also requires updating/rebuilding the AWS worker.


## Media verification failures

`MEDIA_NOT_CONFIRMED_OFF` means the browser stopped before joining because it could not prove both devices were off. This is separate from OAuth verification and host admission. The media check recognises explicit action labels in either word order (for example, “Turn on microphone” or “Turn microphone on”), supports control tooltip labels, filters hidden duplicate controls before selecting the first match, and retries briefly for delayed rendering/state changes. It never treats a missing device, arbitrary page text or a generic settings button as proof that media is off, and does not repeatedly click a toggle while waiting for confirmation.

New failed sessions store bounded media-check outcomes, identifying which device was confirmed off. They show `NO_ATTENDANCE`, and do not automatically poll Google or charge a summary model. Legacy failed sessions still in the summary queue are corrected when processed by the updated worker. An explicit transcript refresh after the scheduled meeting end can still request a Google transcript available to the connected account.

After deploying/rebuilding the AWS worker, test with a new event 5–10 minutes ahead. Old failed attendance attempts are not automatically replayed. If the failure remains, share the displayed media-check outcomes; an actual Google UI/permission prompt may need further handling. Local Chromium regression tests exercise representative controls, not Google's live page.


## Inspect the actual failed pre-join screen

The AWS worker explicitly grants its virtual microphone/camera permission to the Meet origin before navigation. The media-off proof remains mandatory. If that still fails, **View notes → Failed pre-join screen** shows the actual browser screen and visible media/join/permission control labels. The capture happens before joining, only on the exact HTTPS Meet host; Google sign-in pages are excluded. Input fields and account controls are masked, but meeting information may still be visible. Screenshots are bounded to 256 KB before encryption, stored encrypted with the session, excluded from session-list responses, and decrypted only in the owning account's detail view. The usual session deletion and worker retention purge remove them. They are never sent to AssemblyAI or the summary model.

Earlier failed sessions cannot gain a screenshot retroactively. After updating the worker, create a fresh event; if it fails, inspect the captured screen instead of guessing which Google UI the worker encountered. Browser-control fixture tests do not prove a live meeting's pre-join UI matches the fixtures.

`Dockerfile.meet` now installs system/Python/Chromium dependencies before copying source code, deriving the dependency list from `pyproject.toml`. The first build after this Dockerfile change rebuilds those layers once. Subsequent source-only builds can reuse them if the Docker cache remains available. Dependency or base-image changes still require rebuilding them.


## Google-account attendance pilot

Optional persistent Google browser profiles and operator-assisted SSH-tunnelled sign-in are now available. See [AWS Google sign-in setup](AWS_GOOGLE_SIGNIN.md). Set `MEET_CLOUD_IDENTITY_MODE=google_profile` only after configuring that workflow. This replaces guest identity for that mode; it does not exchange API tokens for browser cookies, and requires live Google sign-in and admission testing. Earlier descriptions of anonymous-only behavior apply to `guest` mode.
