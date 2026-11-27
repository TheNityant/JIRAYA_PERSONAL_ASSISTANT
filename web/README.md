# Jiraya Web

The Next.js frontend provides account sign-in, service connections, an automation
builder, and client/assistant email workspace views. See the [email workspace
walkthrough](../docs/EMAIL_ASSIGNMENT.md) for sample inbox and live Gmail behavior.

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

On macOS or Linux, use `cp .env.local.example .env.local` instead of `Copy-Item`.

Open `http://127.0.0.1:3000`. Create an account/sign in, then connect services through the first-run connection screen.

The UI stores automation definitions through the FastAPI `/automations` API.
Gmail rules support sender/subject/body filters, a semantic condition, user
context, action configuration, approval policy, and a match preview.

The existing Verifier -> Reconciliator -> Obligation pipeline remains backend-owned; the visual nodes expose that lifecycle instead of delegating orchestration to n8n.
