import asyncio
from contextlib import asynccontextmanager, suppress

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
    events,
    github,
    gmail,
    gmail_runtime,
    health,
    notifications,
    reconciliation,
)
from app.runtime.background import calendar_monitor_loop, github_monitor_loop, gmail_monitor_loop

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    monitor_task: asyncio.Task | None = None
    if settings.gmail_background_monitor_enabled:
        monitor_task = asyncio.create_task(gmail_monitor_loop(settings))
    calendar_task = (asyncio.create_task(calendar_monitor_loop(settings))
                     if settings.calendar_background_monitor_enabled else None)
    github_task = (asyncio.create_task(github_monitor_loop(settings))
                   if settings.github_background_monitor_enabled else None)
    try:
        yield
    finally:
        for task in (monitor_task, calendar_task, github_task):
            if task:
                task.cancel()
        for task in (monitor_task, calendar_task, github_task):
            if task:
                with suppress(asyncio.CancelledError):
                    await task


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
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(gmail.router)
app.include_router(gmail_runtime.router)
app.include_router(calendar.router)
app.include_router(github.router)
app.include_router(reconciliation.router)
app.include_router(automations.router)
app.include_router(automation_execution.router)
app.include_router(connections.router)
app.include_router(notifications.router)


@app.get("/")
def root() -> dict:
    return {
        "name": settings.app_name,
        "version": "0.5.0",
        "phase": "phase-2e",
        "docs": "/docs",
    }
