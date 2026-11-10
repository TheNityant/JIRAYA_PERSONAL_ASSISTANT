# Guest attendance on main

The Google browser sign-in implementation is preserved on branch `archive/meet-google-browser-signin-2026-11-10`. Main defaults to `MEET_CLOUD_GUEST_ONLY=true`, which overrides older Google-profile/portal environment values in the worker and API. Guest attempts use a fresh temporary browser profile, never the saved Google cookies, and do not require browser sign-in. The optional Google transcript API permission remains separate.

Guest display names prefer the Jiraya user profile, then a non-email Calendar display name, then an email local part, then Jiraya. A display name is not Google-authenticated identity. The host should know the guest is an automated note taker and must permit transcription. Microphone and camera OFF verification still gates admission; no selectors or media protections are bypassed. Google/host admission restrictions still apply, including possible rejection of cloud browsers even when another incognito browser works.

After deploying main to Render and Vercel, update AWS:

```bash
cd ~/JIRAYA_PERSONAL_ASSISTANT
git pull --ff-only origin main
nano .env.meet
```

Set:

```dotenv
MEET_CLOUD_GUEST_ONLY=true
MEET_CLOUD_IDENTITY_MODE=guest
MEET_CLOUD_BROWSER_PORTAL_ENABLED=false
```

```bash
sudo docker compose -f deploy/meet/compose.yaml up -d --build meet-worker
```

No new frontend AWS URL or Google browser login is needed. Keep the existing database, encryption key and provider keys. Old browser profile files are not automatically deleted, but guest mode does not read them. Test a fresh Calendar meeting with guests allowed; admit the name shown in Jiraya, confirm devices off, verify speech capture and an explicit leave command, then inspect notes. Rejections remain explicit diagnostics, not a guarantee that every Google meeting accepts the bot.

## Native headed browser comparison

After the manual AWS browser reached guest pre-join and admission, the guest worker defaults to `MEET_CLOUD_BROWSER_DRIVER=native`. It launches ordinary headed Chromium under Xvfb with a fresh temporary profile, then attaches a loopback-only CDP connection to operate it. This removes the Playwright launch defaults and fake-device flags; it does not mask browser identity, automate Google sign-in, or bypass Google checks. Device OFF verification still gates joining and inbound meeting audio is routed to the private PulseAudio sink. The new CDP connection remains a difference from the successful manual test, so live admission must be retested. There is no CDP/VNC port published on the host.

Stop the manual diagnostic first (Ctrl+C or close its Chromium window), then pull main and rebuild/restart the worker. `MEET_CLOUD_BROWSER_DRIVER=playwright` retains the previous launch implementation for controlled comparisons. No frontend changes are required for this switch.
