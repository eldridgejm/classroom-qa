"""
Integration test for question creation SSE flow

Tests the complete flow:
1. Admin creates question via HTTP POST
2. SSE event is published to Redis
3. Student SSE stream receives the event
"""

import asyncio
import json

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from redis import Redis

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings
from app.models import EventType
from app.redis_client import RedisClient


class TestQuestionSSEFlow:
    """Test question creation SSE flow"""

    def test_admin_creates_question_events_published(
        self, client: TestClient, test_settings: Settings, redis_client: Redis
    ) -> None:
        """
        Test that creating a question publishes an event to Redis
        """
        # Setup
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")

        # Create pubsub subscriber BEFORE creating question
        pubsub = redis_client.pubsub()
        channel = "course:test-course:events"
        pubsub.subscribe(channel)

        # Discard the subscribe confirmation message
        msg = pubsub.get_message(timeout=1.0)
        assert msg is not None
        assert msg["type"] == "subscribe"

        # Create MCQ question
        response = client.post(
            "/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        )

        print(f"\nQuestion create response: {response.status_code}")
        print(f"Response body: {response.text}")
        assert response.status_code == 200
        data = response.json()
        question_id = data["question_id"]
        print(f"Created question: {question_id}")

        # Wait for the published event
        received = False
        for i in range(10):  # Try for 1 second
            msg = pubsub.get_message(timeout=0.1)
            if msg and msg["type"] == "message":
                print(f"Received message: {msg}")
                event_data = json.loads(msg["data"])
                print(f"Event data: {event_data}")

                # Verify the event
                assert event_data["event"] == EventType.QUESTION_STARTED.value
                assert event_data["data"]["question_id"] == question_id
                assert event_data["data"]["type"] == "mcq"
                assert event_data["data"]["options"] == ["A", "B", "C", "D"]

                received = True
                break

        pubsub.close()

        assert received, "Did not receive question_started event from Redis pub/sub"

    @pytest.mark.asyncio
    async def test_sse_stream_delivers_question_event(
        self, test_settings: Settings
    ) -> None:
        """
        Test that SSE stream delivers question_started events to students
        """
        from app.routes.sse import event_generator

        # Setup Redis for publishing
        import redis
        publisher_conn = redis.from_url(test_settings.redis_url, decode_responses=True)
        publisher_conn.flushdb()
        publisher = RedisClient(publisher_conn)

        # Start the SSE event generator
        events_received = []

        async def consume_events():
            """Consume events from the generator"""
            try:
                async for event in event_generator("test-course", filter_counts=True):
                    print(f"Received SSE event: {event}")
                    events_received.append(event)
                    # Stop after first real event (not the ": connected" comment)
                    if event.startswith("event:"):
                        break
            except asyncio.CancelledError:
                pass

        # Start consuming events in background
        consumer_task = asyncio.create_task(consume_events())

        # Give time for subscription to register
        await asyncio.sleep(0.2)

        # Publish a question_started event
        publisher.publish_event(
            "test-course",
            EventType.QUESTION_STARTED,
            {
                "question_id": "q-test-123",
                "type": "mcq",
                "options": ["A", "B", "C", "D"],
            },
        )

        # Wait for event to be received
        try:
            await asyncio.wait_for(consumer_task, timeout=2.0)
        except asyncio.TimeoutError:
            consumer_task.cancel()

        print(f"\nEvents received: {events_received}")

        # Verify at least 2 events: connection comment + actual event
        assert len(events_received) >= 2, f"Expected at least 2 events, got {len(events_received)}"

        # Find the question_started event
        question_event = None
        for event in events_received:
            if "event: question_started" in event:
                question_event = event
                break

        assert question_event is not None, "Did not find question_started event"
        assert "q-test-123" in question_event
        assert "mcq" in question_event

        publisher_conn.close()

    def test_form_data_question_creates_with_options(
        self, client: TestClient, test_settings: Settings, redis_client: Redis
    ) -> None:
        """
        Test that form data with options array works correctly
        This tests the specific HTMX use case
        """
        # Setup
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")

        # Create MCQ question with form data (simulating HTMX)
        response = client.post(
            "/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        data = response.json()
        question_id = data["question_id"]

        # Verify question was created with correct options
        question_meta = redis_wrapper.get_question_meta("test-course", question_id)
        assert question_meta is not None
        assert question_meta["options"] == ["A", "B", "C", "D"]
        print(f"\nQuestion metadata: {question_meta}")
