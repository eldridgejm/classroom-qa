"""
Server-Sent Events (SSE) routes for real-time updates
"""

import asyncio
import json
from typing import Annotated, AsyncGenerator

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import StreamingResponse

import app.config
from app.auth import require_tsn
from app.models import EventType
from app.routes import admin as admin_routes

router = APIRouter()

# Seconds of silence before the stream sends a heartbeat event
HEARTBEAT_INTERVAL = 15

# Most live streams one student may hold open at once. A student with a few
# tabs open stays under this, but a browser that keeps opening new streams
# without closing the old ones (seen in lecture on 2026-11-03, when something
# on one laptop swallowed the stream so Chrome reconnected every 3 s) would
# otherwise pile up hundreds of streams and use up nginx's connections.
# Opening one more ends that student's oldest stream.
MAX_STREAMS_PER_STUDENT = 4

# Live student streams, oldest first, keyed by (course, TSN). Each entry is
# the event that tells that stream to end. (The app runs as one process.)
_student_streams: dict[tuple[str, str], list[asyncio.Event]] = {}


def _register_student_stream(key: tuple[str, str]) -> asyncio.Event:
    """Record a new stream for a student, ending their oldest ones over the cap"""
    stop = asyncio.Event()
    streams = _student_streams.setdefault(key, [])
    streams.append(stop)
    while len(streams) > MAX_STREAMS_PER_STUDENT:
        streams.pop(0).set()
    return stop


def _unregister_student_stream(key: tuple[str, str], stop: asyncio.Event) -> None:
    streams = _student_streams.get(key)
    if streams is None:
        return
    if stop in streams:
        streams.remove(stop)
    if not streams:
        del _student_streams[key]


# Dependency to verify admin authentication (password login or remembered device)
verify_admin_auth = admin_routes.verify_admin_auth


# Dependency to verify TSN authentication
def verify_tsn_auth(
    student_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Verify TSN authentication and return TSN"""
    return require_tsn(student_session, app.config.settings.secret_key)


async def event_generator(
    course: str,
    filter_counts: bool = False,
    student_tsn: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generate SSE events from Redis pub/sub

    Args:
        course: Course slug
        filter_counts: If True, filter out counts_updated events (for students)
        student_tsn: For a student stream, the student's TSN; the stream then
            counts toward MAX_STREAMS_PER_STUDENT and ends if it is evicted

    Yields:
        SSE formatted event strings
    """
    import redis.asyncio as aioredis

    # Create async Redis connection
    redis_conn = await aioredis.from_url(
        app.config.settings.redis_url,
        decode_responses=True,
    )

    pubsub = redis_conn.pubsub()
    channel = f"course:{course}:events"

    stream_key = (course, student_tsn) if student_tsn is not None else None
    stop = _register_student_stream(stream_key) if stream_key is not None else None

    try:
        # Subscribe to course events channel
        await pubsub.subscribe(channel)

        # Send initial comment to keep connection alive
        yield ": connected\n\n"

        # Listen for messages, sending a heartbeat whenever the channel is quiet.
        # The heartbeat is a named event (not a comment) so clients can see it
        # and detect connections that have silently died.
        loop = asyncio.get_running_loop()
        last_sent = loop.time()
        while stop is None or not stop.is_set():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )

            if message is None:
                if loop.time() - last_sent >= HEARTBEAT_INTERVAL:
                    last_sent = loop.time()
                    yield "event: ping\ndata: {}\n\n"
                continue

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

                    last_sent = loop.time()
                    yield sse_event

                except (json.JSONDecodeError, KeyError) as e:
                    # Skip malformed events
                    print(f"Error parsing SSE event: {e}")
                    continue

    except asyncio.CancelledError:
        # Client disconnected
        pass
    finally:
        if stream_key is not None and stop is not None:
            _unregister_student_stream(stream_key, stop)
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
    tsn: Annotated[str, Depends(verify_tsn_auth)],
) -> StreamingResponse:
    """
    SSE stream for students (filters out counts_updated events)
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return StreamingResponse(
        event_generator(course, filter_counts=True, student_tsn=tsn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
