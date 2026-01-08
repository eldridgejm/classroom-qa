"""
Integration test for SSE flow

This test simulates the actual end-to-end flow:
1. Admin starts session
2. Admin creates question
3. Student connects to SSE
4. Student receives question_started event
"""

import asyncio
import json

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings


class TestSSEIntegration:
    """Integration tests for SSE event flow"""

    def test_admin_creates_question_student_receives_sse(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """
        Test complete flow: admin creates question, event is published to Redis
        (Students would receive this via SSE, which is tested separately)
        """
        # Setup admin cookie
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_wrapper = RedisClient(redis_client)

        # 1. Admin starts session
        response = client.post(
            "/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200
        print(f"\n✓ Session start response: {response.status_code}")

        # 2. Subscribe to Redis pub/sub channel BEFORE creating question
        pubsub = redis_client.pubsub()
        channel = "course:test-course:events"
        pubsub.subscribe(channel)

        # Discard the subscribe confirmation message
        msg = pubsub.get_message(timeout=1.0)
        assert msg is not None
        assert msg["type"] == "subscribe"
        print("✓ Subscribed to Redis pub/sub channel")

        # 3. Admin creates MCQ question (using form data)
        response = client.post(
            "/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        )
        print(f"✓ Question create status: {response.status_code}")
        assert response.status_code == 200
        data = response.json()
        question_id = data["question_id"]
        print(f"✓ Created question: {question_id}")

        # 4. Wait for and verify the published event
        received_event = None
        for i in range(10):  # Try for 1 second
            msg = pubsub.get_message(timeout=0.1)
            if msg and msg["type"] == "message":
                print(f"✓ Received Redis message: {msg['data']}")
                event_data = json.loads(msg["data"])

                # Verify the event
                assert event_data["event"] == "question_started"
                assert event_data["data"]["question_id"] == question_id
                assert event_data["data"]["type"] == "mcq"
                assert event_data["data"]["options"] == ["A", "B", "C", "D"]

                received_event = event_data
                break

        pubsub.close()

        # 5. Verify event was received
        assert received_event is not None, "Student did not receive question_started event from Redis pub/sub"
        print("✓ Event successfully published and received via Redis pub/sub")
        print(f"✓ This event would be delivered to students via SSE")

    @pytest.mark.asyncio
    async def test_redis_pubsub_direct(self, test_settings: Settings) -> None:
        """
        Test Redis pub/sub directly to verify events are being published
        """
        from app.models import EventType
        from app.redis_client import RedisClient

        # Create async Redis connection for subscriber
        subscriber = await aioredis.from_url(
            test_settings.redis_url,
            decode_responses=True,
        )

        # Create sync Redis connection for publisher
        import redis
        publisher_conn = redis.from_url(test_settings.redis_url, decode_responses=True)
        publisher = RedisClient(publisher_conn)

        try:
            # Subscribe to course events
            pubsub = subscriber.pubsub()
            await pubsub.subscribe("course:test-course:events")

            # Give subscription time to register
            await asyncio.sleep(0.1)

            # Publish an event
            publisher.publish_event(
                "test-course",
                EventType.QUESTION_STARTED,
                {
                    "question_id": "q-test-123",
                    "type": "mcq",
                    "options": ["A", "B", "C", "D"],
                },
            )

            # Wait for message
            received = False
            for _ in range(10):  # Try for 1 second
                message = await pubsub.get_message(timeout=0.1)
                if message and message["type"] == "message":
                    data = json.loads(message["data"])
                    print(f"Received Redis message: {data}")
                    assert data["event"] == EventType.QUESTION_STARTED.value
                    assert data["data"]["question_id"] == "q-test-123"
                    received = True
                    break
                await asyncio.sleep(0.1)

            assert received, "Did not receive published event from Redis"

        finally:
            await pubsub.unsubscribe("course:test-course:events")
            await pubsub.close()
            await subscriber.close()
