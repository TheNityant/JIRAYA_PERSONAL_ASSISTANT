from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routes import ai, events, gmail, health


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Phase-1 Jiraya API: Nebius/Nemotron reasoning, PostgreSQL Event Ledger, "
        "and a safety-gated Gmail Agent -> Verifier vertical slice."
    ),
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(ai.router)
app.include_router(events.router)
app.include_router(gmail.router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": "0.2.0", "phase": "phase-1", "docs": "/docs"}
