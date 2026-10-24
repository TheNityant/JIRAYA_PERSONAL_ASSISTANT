# What is an agent in Jiraya?

Jiraya currently implements LLM-assisted automation workflows. Python orchestrates a user-configured trigger and action; bounded model calls interpret events, draft proposals and assess whether an action is covered by the instructions. It is not an open-ended multi-agent planner.

## Responsibilities in the current code

| Responsibility | Implementation | Model makes decisions? |
| --- | --- | --- |
| Match incoming email | `app/automation/matcher.py` (`AutomationMatcher`) | Yes, after deterministic filters |
| Decide whether an automatic action is within scope | `app/automation/action_router.py` (`AutomationActionRouter`) | Yes; application code enforces the result |
| Draft an email reply | `app/automation/draft_generator.py` (`DraftReplyGenerator`) | Yes |
| Propose a Calendar event | `app/automation/calendar_action.py` (`CalendarEventAction`) | Yes; code validates dates, enforces policy and verifies writes |
| Propose GitHub work | `app/automation/github_action.py` (`GitHubAction`) | Issue content uses the model; branch/repository configuration and execution are constrained by code |
| Interpret outstanding commitments and later evidence | `app/automation/obligation_extractor.py`, `app/reconciler/obligation_resolver.py`, `app/reconciler/email_reconciler.py` | Yes |
| Execute and enforce workflow policy | `app/automation/executor.py`, `app/automation/reliable_executor.py` | Python orchestration |
| Call Gmail, Calendar and GitHub | `app/integrations/gmail/client.py`, `app/integrations/calendar/client.py`, `app/integrations/github/client.py` | No; these are provider adapters/tools |
| Verify Gmail execution | `app/verifier/gmail_verifier.py` | No; provider evidence |
| Monitor connected accounts | `app/runtime/background.py`, `gmail_runtime.py`, `calendar_runtime.py`, `github_runtime.py` | Polling and orchestration; event interpretation may call the model |
| Access the language model | `app/providers/nebius.py` | Model provider boundary |

An agent usually selects actions/tools in pursuit of a goal, observes results, and decides what to do next. A tool performs a bounded operation. Automation can be fully deterministic, model-assisted, or agent-driven. Multiple model-backed classes do not by themselves establish independent autonomous agents.

Jiraya has bounded agent-like decision stages, but its executor follows the configured action rather than letting the model repeatedly choose arbitrary tools. There is no MCP client/server layer in this application. MCP standardizes how clients discover and call tools; an agent can also call ordinary Python functions or HTTP APIs directly.

## Routes the automation builder can execute today

| Trigger | Actions |
| --- | --- |
| Gmail: new email | Reply, notify, create Calendar event, create GitHub issue, create GitHub branch |
| Calendar: event changed | Notify |
| GitHub: issue, pull request or workflow changed | Notify |

The builder displays connected trigger services, the selected route and its action destination. It is a view of a single configured workflow, not an arbitrary graph execution engine. Provider actions appear only when their required services are connected. Calendar-to-GitHub writes, pushing commits, fork synchronization, arbitrary chaining and a general autonomous planner are not implemented by this visual graph.

## Why FastAPI remains sufficient

FastAPI is already the backend framework. It can serve authentication, OAuth callbacks, user-scoped database access and integration endpoints in production. REST is an API architectural style, not a competing framework; FastAPI can implement REST APIs.

Django provides an integrated ORM, migrations, authentication, admin UI and other application conventions. Those are useful when they reduce implementation work, especially for content-heavy products and staff administration. Adding Django does not inherently reduce LLM latency, external API latency or polling delays. A framework migration should follow a concrete need or measured bottleneck, not an assumption that deployment requires it.

## Calendar recurrence display

The calendar separates the selected day (Today by default) from Upcoming, grouped by local date. Tomorrow’s events are never promoted as today’s featured event. Date-only events use local midnight, all-day end dates remain exclusive, and Today advances after local midnight.

The agenda defaults to a bounded 30-day window; the frontend offers 7, 30 and 90 days and adjacent windows. Google expands recurring events into instances, so an unbounded request can return the same birthday in future years. Bounding the query resolves that display issue without deleting legitimate occurrences. Only duplicate provider IDs are deduplicated. All-day event end dates are exclusive and are displayed accordingly.

## Validation boundary

Automated tests use controlled model/provider responses. Frontend smoke tests use mocked API responses and exercise rendering and interactions. Neither establishes that a particular deployed OAuth client, token scope or real provider account is currently healthy; that requires live account testing.

## Proposed Hermes worker

See [HERMES_AGENT_PROPOSAL.md](HERMES_AGENT_PROPOSAL.md) for the proposed cloud agent boundary. A bounded GitHub-only briefing pilot now calls a configured Hermes API with pre-collected metadata; see [HERMES_BRIEFING.md](HERMES_BRIEFING.md). Dynamic agent-selected tools and queued cloud workers remain future work. The current automation executor and provider adapters remain in use.

Calendar display checks run from `web`: `node --test tests/calendar-agenda.cjs` and `node tests/calendar-browser.cjs` (the latter requires Playwright and Chromium). The browser regression uses mocked provider responses and a temporary route on port 3118; it covers Today/Upcoming separation, local-midnight rollover, range navigation, mobile layout and stale-data clearing after an error.
