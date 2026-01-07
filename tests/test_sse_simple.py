"""
Simple SSE test to verify the endpoint works correctly
"""

import asyncio
import json

import pytest
import redis.asyncio as aioredis

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings
from app.models import EventType
from app.redis_client import RedisClient


@pytest.mark.asyncio
async def test_sse_stream_format(test_settings: Settings, redis_client) -> None:
    """
    Test that SSE endpoint formats events correctly
    """
    from app.routes.sse import event_generator

    # Set up test data
    student_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

    # Start session
    redis_wrapper = RedisClient(redis_client)
    redis_wrapper.start_session("test-course")

    # Start SSE generator
    gen = event_generator("test-course", filter_counts=False)

    # Get initial connection message
    first_message = await gen.__anext__()
    print(f"First message: {repr(first_message)}")
    assert first_message == ": connected\n\n"

    # Publish an event in background
    async def publish_event():
        await asyncio.sleep(0.1)
        redis_wrapper.publish_event(
            "test-course",
            EventType.QUESTION_STARTED,
            {
                "question_id": "q-test-123",
                "type": "mcq",
                "options": ["A", "B", "C", "D"],
            },
        )

    task = asyncio.create_task(publish_event())

    # Wait for published event
    try:
        event_message = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        print(f"Event message: {repr(event_message)}")

        # Verify format
        assert "event: question_started\n" in event_message
        assert "data: " in event_message
        assert event_message.endswith("\n\n")

        # Extract and parse data
        lines = event_message.strip().split("\n")
        event_line = [l for l in lines if l.startswith("event:")][0]
        data_line = [l for l in lines if l.startswith("data:")][0]

        event_type = event_line.split(":", 1)[1].strip()
        data_json = data_line.split(":", 1)[1].strip()
        data = json.loads(data_json)

        assert event_type == "question_started"
        assert data["question_id"] == "q-test-123"
        assert data["type"] == "mcq"

    finally:
        await task
        # Close generator
        await gen.aclose()


@pytest.mark.asyncio
async def test_sse_htmx_compatibility(test_settings: Settings, redis_client) -> None:
    """
    Verify SSE output is compatible with HTMX expectations

    HTMX SSE extension expects:
    - event: <event_name>
    - data: <json_data>
    - blank line
    """
    from app.routes.sse import event_generator

    redis_wrapper = RedisClient(redis_client)
    redis_wrapper.start_session("test-course")

    gen = event_generator("test-course", filter_counts=False)

    # Skip connection message
    await gen.__anext__()

    # Publish test event
    redis_wrapper.publish_event(
        "test-course",
        EventType.QUESTION_STARTED,
        {"question_id": "q-123", "type": "mcq", "options": ["A", "B"]},
    )

    try:
        # Get the event
        event_str = await asyncio.wait_for(gen.__anext__(), timeout=1.0)

        # Should be formatted as:
        # event: question_started\n
        # data: {"question_id": "q-123", ...}\n
        # \n
        print(f"Event string: {repr(event_str)}")

        # Verify double newline at end (required by SSE spec)
        assert event_str.endswith("\n\n"), f"Event should end with \\n\\n, got: {repr(event_str[-10:])}"

        # Verify event line
        assert event_str.startswith("event: "), f"Event should start with 'event: ', got: {repr(event_str[:50])}"

        # Verify data line
        assert "\ndata: " in event_str, f"Event should contain data line, got: {repr(event_str)}"

    finally:
        await gen.aclose()
