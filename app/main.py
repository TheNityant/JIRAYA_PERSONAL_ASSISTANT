from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import init_db
from app.routes import ai, events, health


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Phase-0 API for Jiraya: Token Factory provider and append-oriented Event Ledger.",
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(ai.router)
app.include_router(events.router)


@app.get("/")
def root() -> dict:
    return {"name": settings.app_name, "version": "0.1.0", "phase": "phase-0", "docs": "/docs"}
