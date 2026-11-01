"""
Main FastAPI application for In-Class Q&A + Polling Tool
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import redis
from fastapi import FastAPI

from app import config
from app.redis_client import RedisClient
from app.routes import admin, sse, student
from app.services.sessions import stop_idle_sessions

logger = logging.getLogger(__name__)


def check_idle_sessions_once() -> None:
    """Stop every course's session that has been idle for too long"""
    settings = config.settings
    redis_conn = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        stop_idle_sessions(RedisClient(redis_conn), settings)
    finally:
        redis_conn.close()


async def idle_session_monitor() -> None:
    """Background task: periodically auto-stop idle sessions"""
    while True:
        await asyncio.sleep(max(1, config.settings.idle_check_interval))
        if config.settings.idle_session_timeout <= 0:
            continue
        try:
            # Redis calls are blocking; keep them off the event loop
            await asyncio.to_thread(check_idle_sessions_once)
        except Exception:
            logger.exception("Idle session check failed")


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(idle_session_monitor())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Classroom Q&A",
    description="In-Class Q&A + Polling Tool for UCSD Data Science Lectures",
    version="0.1.0",
    lifespan=lifespan,
)


# Define health endpoints before including routers
# (so they take precedence over the /{course} routes)
@app.get("/")
async def root() -> dict[str, str]:
    """Health check endpoint"""
    return {"status": "ok", "message": "Classroom Q&A API"}


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint for monitoring"""
    return {"status": "healthy"}


# Include routers
app.include_router(admin.router, tags=["admin"])
app.include_router(student.router, tags=["student"])
app.include_router(sse.router, tags=["sse"])
