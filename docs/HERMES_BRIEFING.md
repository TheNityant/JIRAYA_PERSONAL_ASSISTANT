# Hermes developer briefing: initial pilot

## What is implemented

GitHub now has **Hermes · Read-only pilot → Across your projects**. Select one to three loaded repositories and click **Generate developer briefing**. Jiraya reads up to six recent pull requests, six recent issues and four recent workflow runs per repository, then sends a bounded metadata snapshot to a configured Hermes HTTP API. The result includes source links and per-source coverage/failures.

This milestone is a bounded briefing, not autonomous tool selection. Jiraya collects the evidence before Hermes summarizes it. No GitHub writes, repository clones, code reads, email reads or Calendar reads are implemented by this feature. Existing watchers and local checkpoints continue independently. Comparing usefulness, latency and cost with existing model calls is the evaluation goal; using Hermes alone is not evidence of an improvement.

The feature defaults to disabled. Implementing the adapter does not launch a DigitalOcean session or fund a model. Real cloud inference has not been verified by the fixture-based tests.

## App-owner setup

### Is a separate Hermes runtime necessary?

No. The core Gmail, Calendar, GitHub watchers and local checkpoints do not depend on Hermes. The current pilot sends a bounded metadata snapshot for summarization with native toolsets disabled; equivalent summarization could use the existing model adapter without another agent runtime. This alternative has not been implemented by the Hermes pilot. Before paying for a runtime, compare factual accuracy, useful next steps, latency and per-briefing cost against that simpler baseline. Autonomous multi-step investigations would be a stronger reason to revisit a separate agent, with scoped tools and durable jobs first. The temporary frontend demo has been removed; it was not evidence of a live integration.

Use a dedicated Hermes runtime/profile for one pilot user. This version rejects all other Jiraya user IDs; it is not a shared multi-user agent. The profile must have every native toolset disabled. Keep it separate from personal Hermes memory, credentials, cron jobs and unrelated conversations. The adapter checks `GET /v1/toolsets` before reading GitHub data and fails closed unless every returned toolset explicitly has `enabled: false` (an empty list is accepted). Prompt instructions alone do not enforce tool permissions.

1. Follow [DigitalOcean's Hermes guide](https://docs.digitalocean.com/products/managed-agents/agent-harness-runtime/how-to/run-hermes-agent/) to create an isolated test runtime with a model and inference credential. Do not connect GitHub directly to that runtime for this pilot; Jiraya uses its existing connection to supply metadata.
2. Configure the dedicated profile's authenticated [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server). Disable all toolsets in the profile's configuration and verify `/v1/toolsets`. The inference credential and Hermes API server key are separate credentials.
3. Make the API reachable privately from the Jiraya backend. DigitalOcean's forwarded dashboard on port 9119 is not the API endpoint. If the Hermes API is enabled on port 8642 inside the sandbox, a local development test can forward that port with `doctl harness-runtime port-forward <session-id> 8642`; verify it is listening rather than assuming the template enables it. Keep the tunnel running. A hosted backend needs its own private connection; a tunnel on your laptop is not available to a remote backend.
4. Find the intended user's ID in authenticated `GET /auth/me` (`user.id`). Configure the Jiraya backend once with these operator settings, using local `.env` or the host's secret manager:

```dotenv
HERMES_BRIEFING_ENABLED=true
HERMES_BASE_URL=http://127.0.0.1:8642/v1
HERMES_API_KEY=<dedicated-Hermes-API-server-key>
HERMES_MODEL=hermes-agent
HERMES_OWNER_USER_ID=<intended-Jiraya-user-id>
HERMES_TIMEOUT_SECONDS=60
```

Use HTTPS for non-loopback endpoints. A profile-specific URL may end with `/p/<profile>/v1`; use that profile's own key. `HERMES_MODEL` is the model name exposed by Hermes, not necessarily the inference model used inside it. Never put real credentials in `.env.example`, a browser variable or a Git commit. End users do not need to edit backend settings or reconnect GitHub for each repository.

5. Restart the Jiraya backend and frontend. In the GitHub page, click **Check configuration**. This checks operator configuration only; **Generate developer briefing** checks runtime reachability and tool policy before sending data.
6. Select two test repositories with known activity and generate. Compare claims against the linked PRs, issues and workflow runs. A source failure appears under **Sources and coverage**. Empty/inaccessible sources produce coverage information without a model completion. No inference occurs merely by opening the panel.

## Boundaries and failure behavior

- Only selected repository names, titles, states, timestamps, numeric IDs and canonical source links are supplied. OAuth tokens, file contents, issue bodies and comments are not supplied. Metadata may still be private; the UI explains what the Generate button sends.
- Model output is rendered as text. Suggested actions do not call an executor. Model citations and conclusions remain unverified suggestions; check the separate source list.
- Requests require Jiraya login in authenticated mode and resolve GitHub credentials from that user's identity. The request cannot specify a different user or an arbitrary Hermes URL.
- One briefing may run at a time per API process. Run one process for this initial pilot. This is not a distributed lock, queue, spending cap or production scheduling system.
- No model retries are submitted automatically. Non-complete responses, timeouts and provider errors are not reported as successful briefings. Closing the page or timing out does not guarantee remote processing stopped. Hermes can retain sent snapshots under its own profile/history retention settings.
- This release does not persist briefing history in Jiraya. Refreshing the page clears the displayed report. The response includes a run ID for correlation.
- Pausing the DigitalOcean session is separate from closing its dashboard or forwarding tunnel. Review actual compute/model usage and credit eligibility in DigitalOcean before a live run. No DigitalOcean costs are incurred by installing this code alone.

## Test the existing multi-repository connector separately

1. Choose repository A in GitHub, open **Pull requests**, and create/save an enabled watcher with action **Notify**. Leave title/body/semantic filters empty for the initial test.
2. Repeat for repository B. Each watcher stores one repository; both stay enabled.
3. Select either watcher in the automation builder and click **Sync GitHub**. It polls all enabled GitHub watchers and establishes initial baselines without notifying about history. If background polling already established them, there may be no new-baseline message.
4. Edit a PR title in repository A. Sync again, then inspect Notifications for A's repository and PR. Repeat in B.
5. Sync again without changes: no duplicate notification should appear. With the default background monitor enabled, waiting for its configured interval is an alternative to pressing Sync.

These watchers work without Hermes. Workspace Checkpoint is a separate device-local test that actually commits/pushes enabled open repositories; use test branches for that operation.

## Automated verification

`python -m pytest tests/test_developer_briefing.py tests/test_github_request_regressions.py -q` covers account scope, bounded metadata, redaction, disabled profiles, provider failure, no retries and independent watcher baselines/notifications.

From `web`, `node tests/developer-briefing.cjs` uses a real local Next frontend and FastAPI backend, temporary SQLite, encrypted fixture credentials and local HTTP GitHub/Hermes fixtures. It requires project Python dependencies plus Playwright/Chromium, and temporarily uses ports 3119, 8000 and 8997. It never contacts DigitalOcean or real model/provider accounts.
