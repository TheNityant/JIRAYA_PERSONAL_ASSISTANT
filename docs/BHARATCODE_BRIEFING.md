# BharatCode GitHub briefings

BharatCode can summarize a bounded activity snapshot from up to three selected repositories. This is the **Across your projects** feature in GitHub, not code review, repository cloning or an action-taking agent. Normal Gmail, Calendar and Meet processing remains on its existing providers.

## Configure the existing Render API

Add these backend environment variables, then deploy the latest `main`:

```dotenv
GITHUB_BRIEFING_PROVIDER=bharatcode
BHARATCODE_BRIEFING_ENABLED=true
BHARATCODE_API_KEY=YOUR_BHARATCODE_KEY
BHARATCODE_BASE_URL=https://bharatcode.ai/api/model/v1
BHARATCODE_MODEL=qwen-3.8-27b
BHARATCODE_TIMEOUT_SECONDS=60
```

Keep the key only on the backend. Do not add it to Vercel public variables, source control or the AWS Meet worker. The API uses the user's existing GitHub connection; no new GitHub scope is requested. Hermes settings are ignored while BharatCode is selected. To disable new briefings, set `BHARATCODE_BRIEFING_ENABLED=false`.

The official [BharatCode API guide](https://bharatcode.ai/build) documents an OpenAI-compatible Chat Completions endpoint. API access requires an eligible paid or verified student account. The default model is documented for student, Lite and Pro access; verify availability for your account. No new Python dependency is needed.

## Data and cost boundaries

Generating a briefing reads recent PR, issue and workflow metadata from selected repositories. Titles, states, timestamps and links are transmitted, including private repository metadata if selected. Source files, issue bodies, comments and GitHub tokens are excluded. Repository metadata is treated as untrusted input. Suggestions are rendered as text and never execute tools or changes.

One model request is submitted per manual generation, with a 1,600-token output budget and bounded input/response sizes. Opening the panel does not invoke inference. Empty snapshots skip inference. Failures do not retry automatically or fall back to another provider. There is one concurrent briefing per API process, but **no global daily spending limit**; repeated manual generations can incur usage. `store=false` does not replace the provider's retention policy or guarantee that all provider logs are disabled.

## Validate

1. Connect GitHub, open GitHub → Across your projects and check that BharatCode is configured.
2. Choose one or two repositories, then Generate developer briefing.
3. Verify the summary against Sources and coverage. A missing source is reported separately; an HTTP 200 with truncated model output is treated as failure.
4. Try another user account to confirm its own GitHub connection is used.

Automated backend tests use HTTP fixtures for success, empty snapshots, metadata filtering, disabled/missing configuration, upstream errors, timeout, redirects, malformed/oversized responses and tool or incomplete output rejection. The browser regression exercises the real frontend/backend with a Hermes HTTP fixture and the BharatCode UI with controlled API responses. These tests do not prove a live BharatCode key has API access; perform the manual check after deployment.
