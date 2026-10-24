# Proposed Hermes worker for Jiraya

Status: architecture proposal, not a deployed integration. No Hermes endpoint, credentials, MCP server or account access is enabled by this change. Assumes Nous Research's Hermes Agent.

## Purpose

Use Hermes for tasks that require choosing several tools, observing their results and revising a plan. An initial example is a developer briefing: inspect selected pull requests, relevant email and today's calendar, then propose follow-up work. Simple configured notifications and local Git checkpoints can continue through their existing deterministic paths.

Hermes adds agent capabilities; it does not automatically increase Jiraya's user capacity or make provider APIs faster. Scaling still requires durable jobs, bounded worker concurrency, fair scheduling, provider rate-limit handling and database capacity. Those are separate implementation tasks.

## Verified interfaces

Official documentation describes an authenticated HTTP API, asynchronous runs and capability discovery. Hermes also supports MCP clients, which provides a possible bridge to Jiraya tools. Deployment must pin a reviewed Hermes release and verify its advertised capabilities; upstream documentation can change.

- [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
- [Hermes MCP support](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)

These are available upstream capabilities, not existing Jiraya interfaces. Our current application does not expose an MCP server.

## Recommended boundary

| Component | Responsibility in the proposed design |
| --- | --- |
| Frontend | Sign in, request a task, show progress and review action proposals |
| Jiraya FastAPI | Authenticate the user, enforce ownership, create jobs and handle cancellation |
| Durable job queue and worker adapter | Dispatch work to Hermes, track remote run IDs, reconcile failures and enforce budgets |
| Isolated Hermes runtime | Plan, request permitted tools and return structured proposals |
| Jiraya tool broker, later exposed through MCP | Resolve the user from a scoped run identity and call existing provider adapters |
| Existing executor and verifier | Apply configured approval rules, execute approved changes and check provider evidence |

OAuth credentials remain in Jiraya's encrypted credential store. Hermes receives narrowly scoped access to a particular run's allowed tools, not database credentials, shared account tokens or direct access to every user's services. The broker derives user identity from authenticated, short-lived credentials; it rejects a tool argument that attempts to select another user.

A conversation/session identifier groups a conversation. It must not be treated as an authorization boundary. Separately isolate persistent memory, files, skills and any execution environment by user. A proof of concept should use an isolated runtime/profile for one test user; shared multi-user operation needs explicit cross-user isolation tests before rollout.

## First implementation milestone

1. Provision a private test runtime with an explicit capability allowlist and no unrestricted shell, file, credential or provider-write access.
2. Add a backend-only worker adapter and durable task records. Discover supported endpoints before selecting a runs API. Keep service credentials out of browser code.
3. Expose read-only Jiraya tools for selected email metadata, bounded calendar events and selected GitHub repositories. Treat provider text as untrusted input, not authorization to add tools or change policy.
4. Return validated action proposals through Jiraya's existing review flow. Add writes only after identity, approval and duplicate-execution checks pass.
5. Test user isolation, revocation, cancellation, model/provider failures, retries, tool permissions and cost/iteration limits. A completed agent reply alone is not proof that an external action succeeded.

Every run needs a correlation ID and an auditable tool/result trail with secrets redacted. A timeout may mean work is still running: reconcile the original remote run before dispatching another potentially mutating task. Keep Jiraya's action ledger and provider verification even when upstream has its own request deduplication.

Cloud Hermes cannot see the user's desktop repositories merely because Jiraya's browser is paired with the local companion. Local Git remains device-local and requires a separately designed, authenticated device-job protocol for any future cloud-triggered execution. This proposal does not expose port 8765 to the internet.
