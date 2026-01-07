"""
Server-Sent Events (SSE) routes for real-time updates
"""

import asyncio
import json
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import StreamingResponse

import app.config
from app.auth import require_admin, require_pid
from app.models import EventType

router = APIRouter()


# Dependency to verify admin authentication
def verify_admin_auth(
    course: str,
    admin_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Verify admin authentication"""
    require_admin(admin_session, course, app.config.settings)


# Dependency to verify PID authentication
def verify_pid_auth(
    student_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Verify PID authentication and return PID"""
    return require_pid(student_session, app.config.settings.secret_key)


async def event_generator(
    course: str,
    filter_counts: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events from Redis pub/sub

    Args:
        course: Course slug
        filter_counts: If True, filter out counts_updated events (for students)

    Yields:
        SSE formatted event strings
    """
    import redis.asyncio as aioredis

    # Create async Redis connection
    redis_conn = await aioredis.from_url(
        app.config.settings.redis_url,
        decode_responses=True,
    )

    try:
        # Subscribe to course events channel
        pubsub = redis_conn.pubsub()
        channel = f"course:{course}:events"
        await pubsub.subscribe(channel)

        # Send initial comment to keep connection alive
        yield ": connected\n\n"

        # Listen for messages
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    # Parse the event
                    event_data = json.loads(message["data"])
                    event_type = event_data.get("event")
                    data = event_data.get("data", {})

                    # Filter counts_updated events for students
                    if filter_counts and event_type == EventType.COUNTS_UPDATED.value:
                        continue

                    # Format as SSE event
                    sse_event = f"event: {event_type}\n"
                    sse_event += f"data: {json.dumps(data)}\n\n"

                    yield sse_event

                except (json.JSONDecodeError, KeyError) as e:
                    # Skip malformed events
                    print(f"Error parsing SSE event: {e}")
                    continue

    except asyncio.CancelledError:
        # Client disconnected
        pass
    finally:
        # Clean up subscription
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_conn.close()


@router.get("/sse/admin/{course}")
async def admin_sse_stream(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
) -> StreamingResponse:
    """
    SSE stream for admin (receives all events including counts_updated)
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return StreamingResponse(
        event_generator(course, filter_counts=False),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/sse/student/{course}")
async def student_sse_stream(
    course: str,
    _: Annotated[str, Depends(verify_pid_auth)],
) -> StreamingResponse:
    """
    SSE stream for students (filters out counts_updated events)
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return StreamingResponse(
        event_generator(course, filter_counts=True),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
