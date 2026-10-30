# Live integration acceptance: Phase 2e

This is a live-account test plan, not a record of successful live tests. Automated tests use isolated local repositories and provider fixtures. Record the actual result, automation/execution ID, time, and sanitized error for each step. Never paste OAuth credentials, pairing tokens, cookies or email bodies into a public issue.

## Establish the running version

Run `git branch --show-current` and `git log -1 --oneline` in the actual checkout. The current working branch is `phase2e-generic-execution`; `main` does not include all subsequent changes. Restart the backend and local companion after updating Python code. The VS Code extension must be installed separately from its VSIX; pulling source does not upgrade an installed extension.

Verify backend `/health`, sign in through the frontend, and check `/health/db` using the authenticated session. In authenticated mode, a bare unauthenticated database-health request returns 401 rather than a database diagnosis. With browser Network tools open, reload and record any slow or failed request URL, status and duration. A locally hosted frontend is not affected by a Render cold start unless it actually calls a Render backend. Do not infer CPU pressure from a loading message alone.

## Local checkpoint registration: fix before attempting a push

The GitHub OAuth repository picker lists cloud repositories. The Local workspace checkpoint panel lists Git repositories registered by the device companion. These are independent: OAuth cannot discover a Windows folder, and the cloud server cannot run local Git against it.

`Companion connected` plus a registered session proves device connectivity, not valid project registration. If the error says the path is not inside a Git repository, inspect that exact path. In a new PowerShell terminal in the actual checkout:

```powershell
$repoRoot = git rev-parse --show-toplevel
if ($LASTEXITCODE -eq 0) { code -n "$repoRoot" }
```

If Git fails, locate the existing checkout first. Do not initialize a new repository to silence this error. In the opened VS Code window, run **Jiraya: Show Workspace Diagnostics**. Version 0.3.0 reports workspace folders and nested roots already detected by VS Code Git. **Jiraya: Add Git Projects** opens a folder picker; choose each existing checkout, not an outer directory with a similar name. Check the extension is enabled in that window's profile. It does not recursively scan arbitrary disk directories.

| Check | Expected result |
| --- | --- |
| Pair the browser and editor with the running companion | Connected device and an editor session |
| Open the actual checkout / Add Git Projects | One project card with real path and current branch |
| Add a second repository | Two distinct cards; same repository in two windows is deduplicated |
| Select the current local branch and enable each project | Two enabled policies; local checkout must match target branch |
| Save both test projects, then checkpoint | Per-project `PUSHED` or `UP_TO_DATE`; verify remote commit and files on GitHub |
| Leave an unsaved file, then checkpoint | Affected repository is skipped; remaining projects still processed |
| Close its last editor/watch registration | Repository expires from the open list after 45 seconds |

A checkpoint actually commits/pushes saved changes and existing unpushed commits. Use test repositories/branches for the first run. Native Git authentication is separate from GitHub OAuth. Pairing and adding folders do not push anything or launch the project's application server.

## Test independent connections, then supported cross-service routes

Use an exact unique marker such as `JIRAYA-LIVE-001` in each test. Start with Draft Only or Ask before action for actions that write. External writes also require the application owner's `ENABLE_EXTERNAL_ACTIONS` setting. Do not enable Automatic for an unverified rule.

| Route | Live test | Expected evidence |
| --- | --- | --- |
| Gmail read | Send a new marked email after enabling the rule | Correct message/thread in execution history |
| Gmail reply | Generate a reply, approve where configured | Correct recipient/thread, one sent reply and verified result |
| Gmail + Calendar availability | Enable availability checks on Reply to email; send a future explicit-time request | Draft agrees only when selected calendars are free |
| Busy-time alternative | Put a conflicting event on a selected calendar; send another marked request | Draft offers free alternatives within configured hours/timezone |
| No-time request | Send a marked meeting request without dates or times | Draft offers bounded future slots and asks for confirmation |
| Gmail + Calendar booking | Enable booking on a scheduling reply; send one free proposed time | One verified Calendar event before one verified Gmail reply; busy/no-time requests only suggest alternatives |
| Gmail → Calendar event | Send explicit future date, start/end and timezone | Reviewable event proposal; after approval, one provider event with matching values |
| Calendar changed → Notify | Baseline, then edit a marked event | One matching notification; baseline does not replay existing history |
| GitHub read/action | Choose an accessible test repository; create a test branch without spaces | Branch exists on GitHub with the expected source commit |
| GitHub watchers, two repositories | Create one Notify watcher per repo; baseline, change a PR in each, Sync GitHub | Correct independent notifications; unchanged sync produces no duplicates |
| Gmail → GitHub issue | Configure a fixed test repository and marked email rule | Reviewable proposal; after approval, one issue in that repository |
| Account isolation | Sign into a second test account | First account's connections, automations and executions are inaccessible |

Calendar and GitHub change triggers currently support notifications only. They are not arbitrary multi-step workflow chains. Workflow dispatch is a manual GitHub-tab action and requires a workflow with `workflow_dispatch`. Gmail availability replies can optionally book one explicitly proposed free time before replying; enable booking per rule. Suggested alternatives are not booked. They do not create a Meet conference. Event creation sends no invitations in this version.

Removing the temporary briefing demo did not remove these executors. Hermes is optional and is not part of their execution path.

## Meet implementation review gate

No Meet integration is present in the published Phase 2e code at the time this plan was written. Review the contributor's exact branch/PR before choosing a replacement implementation. Successful joining is useful evidence, but review audio capture, transcript content, speaker/timestamp handling, admission rejection, cancellation, credentials/session storage, account isolation and cloud execution separately. A local signed-in browser test does not establish headless Linux/cloud compatibility.

## First cloud deployment

Render is a reasonable initial host for the current FastAPI/Postgres pilot. Use always-on compute and a durable persistent database for unattended polling. The default embedded mode needs one backend instance/process. The Dockerfile installs project dependencies from `pyproject.toml` and listens on the host-provided `PORT` (8000 by default). An optional standalone polling worker is available with `AUTOMATION_RUNTIME_MODE=external` and `python -m app.runtime.worker`; this remains a single-worker runtime, not a distributed queue. See [always-on deployment and closed-laptop acceptance tests](ALWAYS_ON_AUTOMATION.md).

Owner settings: `APP_ENV=production`, `AUTH_REQUIRED=true`, `DATABASE_URL`, stable `TOKEN_ENCRYPTION_KEY`, model credentials, exact frontend origin/base URL, public OAuth callback URLs, and HTTPS cookie settings. Prefer same-site frontend/API domains; cross-site cookies may be blocked even with SameSite=None. For Vercel + Render, use the opt-in [same-origin proxy configuration](RENDER_VERCEL_LOGIN.md), including proxy-based OAuth callbacks. Follow `SERVICE_INTEGRATIONS.md` for configuration details. Disable unrestricted registration for a private pilot after provisioning intended accounts.

Keep PostgreSQL and the API in the same region. A new cloud database does not automatically contain local connections or automation records: reconnect explicitly or perform an intentional database migration retaining ownership and the matching encryption key. The companion and native Git credentials stay on the user's development computer.

More vCPUs do not repair a non-repository path, expired OAuth token, missing scope or wrong callback. Measure memory, CPU, request latency and provider latency before increasing the compute plan. Confirm credit activation, expiration and eligible charges in the hosting dashboard before provisioning; a credit is not an application budget limit.
