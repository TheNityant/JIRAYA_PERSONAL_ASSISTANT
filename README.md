# Jiraya Personal Assistant

Phase-0 implementation for the **Nebius × NVIDIA Global AI Hackathon 2026 — Personal AI** track.

Long-term product loop:

```text
Agent -> Verifier -> Event Ledger -> Reconciliator
```

Phase 0 establishes the foundation only:

- FastAPI backend
- Nebius Token Factory / NVIDIA Nemotron provider
- PostgreSQL Event Ledger
- health checks
- swappable model-provider boundary
- no external write actions yet

## Prerequisites

- Python 3.12+
- Docker Desktop / Docker Engine
- `uv` recommended
- Nebius Token Factory API key

## Setup

```powershell
Copy-Item .env.example .env
```

Set `NEBIUS_API_KEY` in `.env`, then:

```powershell
docker compose up -d db
uv sync --dev
uv run uvicorn app.main:app --reload
```

Open Swagger at `http://127.0.0.1:8000/docs`.

## Phase-0 checks

- `GET /health`
- `GET /health/db`
- `POST /ai/smoke`
- `POST /events`
- `GET /events`
- `uv run pytest`

### Nemotron smoke test

```powershell
$body = @{ message = "Reply with exactly: JIRAYA_NEMOTRON_OK" } | ConvertTo-Json
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/ai/smoke" `
  -ContentType "application/json" `
  -Body $body
```

### Event Ledger smoke test

```powershell
$event = @{
  source = "phase0"
  event_type = "SMOKE_TEST"
  entity_id = "project:jiraya"
  correlation_id = "phase0-001"
  payload = @{ status = "ok" }
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/events" `
  -ContentType "application/json" `
  -Body $event

Invoke-RestMethod "http://127.0.0.1:8000/events"
```

## Next phase

Phase 1 will implement the first real vertical slice:

```text
Incoming Gmail message
  -> Nemotron classification
  -> Agent sends approved reply
  -> Verifier independently checks Gmail
  -> Event Ledger records action + evidence
```

Temporal reconciliation comes only after that path is reliable.

## Security

Never commit `.env`, Gmail OAuth credentials, or tokens. Phase 0 keeps `ENABLE_EXTERNAL_ACTIONS=false`.
