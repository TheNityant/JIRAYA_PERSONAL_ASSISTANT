from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routes import ai, events, gmail, health, reconciliation


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    description=(
        "Phase-2 Jiraya API: verified actions, temporal event observation, and "
        "Gmail reconciliation that distinguishes execution failure from later world changes."
    ),
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(gmail.router)
app.include_router(reconciliation.router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": "0.3.0", "phase": "phase-2", "docs": "/docs"}
