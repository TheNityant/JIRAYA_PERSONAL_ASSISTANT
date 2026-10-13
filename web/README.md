# Jiraya Web

Phase 2C introduces the first user-configurable automation surface.

## Local development

Backend (repository root):

```powershell
python -m uvicorn app.main:app --reload
```

Frontend:

```powershell
cd web
Copy-Item .env.local.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`.

The UI stores automation definitions through the FastAPI `/automations` API. The current vertical slice supports Gmail as the first source, deterministic sender/subject/body filters, a Nemotron semantic condition, user context, action configuration, approval policy, and a live match preview against the latest unread Gmail message.

The existing Verifier -> Reconciliator -> Obligation pipeline remains backend-owned; the visual nodes expose that lifecycle instead of delegating orchestration to n8n.
