from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routes import (
    ai,
    auth,
    automation_execution,
    automations,
    calendar,
    connections,
    developer_briefing,
    events,
    github,
    gmail,
    gmail_runtime,
    health,
    notifications,
    reconciliation,
    runtime,
)
from app.runtime.background import running_monitors

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    current = get_settings()
    if current.automation_runtime_mode == "external":
        yield
    else:
        async with running_monitors(current):
            yield


from app.security.sessions import AccountMiddleware

app = FastAPI(
    title=settings.app_name,
    version="0.5.0",
    description=(
        "Jiraya API: persistent personal automations with Gmail, Google Calendar and "
        "GitHub connections, verified actions, temporal reconciliation, and obligations."
    ),
    lifespan=lifespan,
)
app.add_middleware(AccountMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.frontend_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
from app.routes import meet

app.include_router(meet.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(gmail.router)
app.include_router(gmail_runtime.router)
app.include_router(calendar.router)
app.include_router(github.router)
app.include_router(developer_briefing.router)
app.include_router(reconciliation.router)
app.include_router(automations.router)
app.include_router(automation_execution.router)
app.include_router(connections.router)
app.include_router(notifications.router)
app.include_router(runtime.router)


@app.get("/")
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": "0.5.0",
        "phase": "phase-2e",
        "docs": "/docs",
    }
