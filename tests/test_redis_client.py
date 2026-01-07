"""
Tests for Redis client operations (Phase 2)

This test file covers:
- Session state (start/stop, is_live)
- Current question get/set
- Question metadata CRUD
- Response storage/retrieval
- Count aggregation (MCQ, T/F)
- Atomic answer update (Lua script)
- TTL expiration
- Pub/sub message publishing
- Key generation helpers
"""

import json
import time

import redis

from app.models import EventType, QuestionType
from app.redis_client import RedisClient


class TestKeyGeneration:
    """Test cases for Redis key generation helpers"""

    def test_session_key(self, redis_client: redis.Redis) -> None:
        """Test session key generation"""
        client = RedisClient(redis_client)

        key = client.session_key("test-course")
        assert key == "course:test-course:session:live"

    def test_current_qid_key(self, redis_client: redis.Redis) -> None:
        """Test current question ID key generation"""
        client = RedisClient(redis_client)

        key = client.current_qid_key("test-course")
        assert key == "course:test-course:current_qid"

    def test_question_meta_key(self, redis_client: redis.Redis) -> None:
        """Test question metadata key generation"""
        client = RedisClient(redis_client)

        key = client.question_meta_key("test-course", "q-123")
        assert key == "course:test-course:q:q-123:meta"

    def test_question_responses_key(self, redis_client: redis.Redis) -> None:
        """Test question responses key generation"""
        client = RedisClient(redis_client)

        key = client.question_responses_key("test-course", "q-123")
        assert key == "course:test-course:q:q-123:responses"

    def test_question_counts_key(self, redis_client: redis.Redis) -> None:
        """Test question counts key generation"""
        client = RedisClient(redis_client)

        key = client.question_counts_key("test-course", "q-123")
        assert key == "course:test-course:q:q-123:counts"

    def test_numeric_cache_key(self, redis_client: redis.Redis) -> None:
        """Test numeric cache key generation"""
        client = RedisClient(redis_client)

        key = client.numeric_cache_key("test-course", "q-123")
        assert key == "course:test-course:q:q-123:numeric_cache"

    def test_events_channel_key(self, redis_client: redis.Redis) -> None:
        """Test events channel key generation"""
        client = RedisClient(redis_client)

        key = client.events_channel_key("test-course")
        assert key == "course:test-course:events"


class TestSessionOperations:
    """Test cases for session management operations"""

    def test_start_session(self, redis_client: redis.Redis) -> None:
        """Test starting a session"""
        client = RedisClient(redis_client)

        # Initially no session
        assert not client.is_session_live("test-course")

        # Start session
        client.start_session("test-course")

        # Session should be live
        assert client.is_session_live("test-course")

        # Check Redis directly
        key = client.session_key("test-course")
        assert redis_client.get(key) == "1"

    def test_stop_session_without_ttl(self, redis_client: redis.Redis) -> None:
        """Test stopping a session without TTL"""
        client = RedisClient(redis_client)

        # Start session
        client.start_session("test-course")
        assert client.is_session_live("test-course")

        # Stop session
        client.stop_session("test-course")

        # Session should no longer be live
        assert not client.is_session_live("test-course")

    def test_stop_session_with_ttl(self, redis_client: redis.Redis) -> None:
        """Test stopping a session archives data and clears current session"""
        client = RedisClient(redis_client)

        # Start session and create a question
        client.start_session("test-course")
        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Stop session with TTL
        session_id = client.stop_session("test-course", ttl=10)

        # Session should no longer be live
        assert not client.is_session_live("test-course")

        # Current session keys should be deleted
        session_key = client.session_key("test-course")
        assert redis_client.ttl(session_key) == -2  # Key doesn't exist

        meta_key = client.question_meta_key("test-course", qid)
        assert redis_client.ttl(meta_key) == -2  # Key doesn't exist

        # Archive key should exist with TTL
        archive_key = client.archive_key("test-course", session_id)
        ttl = redis_client.ttl(archive_key)
        assert 0 < ttl <= 10

    def test_is_session_live_nonexistent(self, redis_client: redis.Redis) -> None:
        """Test checking if nonexistent session is live"""
        client = RedisClient(redis_client)

        assert not client.is_session_live("nonexistent-course")

    def test_multiple_courses_sessions(self, redis_client: redis.Redis) -> None:
        """Test that different courses have independent sessions"""
        client = RedisClient(redis_client)

        # Start session for course1
        client.start_session("course1")
        assert client.is_session_live("course1")
        assert not client.is_session_live("course2")

        # Start session for course2
        client.start_session("course2")
        assert client.is_session_live("course1")
        assert client.is_session_live("course2")

        # Stop course1
        client.stop_session("course1")
        assert not client.is_session_live("course1")
        assert client.is_session_live("course2")


class TestCurrentQuestion:
    """Test cases for current question management"""

    def test_get_current_question_none(self, redis_client: redis.Redis) -> None:
        """Test getting current question when none exists"""
        client = RedisClient(redis_client)

        qid = client.get_current_question("test-course")
        assert qid is None

    def test_set_current_question(self, redis_client: redis.Redis) -> None:
        """Test setting current question"""
        client = RedisClient(redis_client)

        client.set_current_question("test-course", "q-123")

        qid = client.get_current_question("test-course")
        assert qid == "q-123"

    def test_clear_current_question(self, redis_client: redis.Redis) -> None:
        """Test clearing current question"""
        client = RedisClient(redis_client)

        client.set_current_question("test-course", "q-123")
        assert client.get_current_question("test-course") == "q-123"

        client.clear_current_question("test-course")
        assert client.get_current_question("test-course") is None

    def test_update_current_question(self, redis_client: redis.Redis) -> None:
        """Test updating current question"""
        client = RedisClient(redis_client)

        client.set_current_question("test-course", "q-123")
        assert client.get_current_question("test-course") == "q-123"

        client.set_current_question("test-course", "q-456")
        assert client.get_current_question("test-course") == "q-456"


class TestQuestionMetadata:
    """Test cases for question metadata CRUD operations"""

    def test_create_question_mcq(self, redis_client: redis.Redis) -> None:
        """Test creating an MCQ question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C", "D"])

        # Should return a question ID
        assert qid is not None
        assert isinstance(qid, str)
        assert qid.startswith("q-")

        # Question should be set as current
        assert client.get_current_question("test-course") == qid

        # Metadata should be stored
        meta = client.get_question_meta("test-course", qid)
        assert meta is not None
        assert meta["id"] == qid
        assert meta["type"] == QuestionType.MCQ.value
        assert meta["options"] == ["A", "B", "C", "D"]
        assert "started_at" in meta
        assert meta["ended_at"] is None

    def test_create_question_tf(self, redis_client: redis.Redis) -> None:
        """Test creating a True/False question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.TF)

        meta = client.get_question_meta("test-course", qid)
        assert meta["type"] == QuestionType.TF.value
        assert meta["options"] is None

    def test_create_question_numeric(self, redis_client: redis.Redis) -> None:
        """Test creating a numeric question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.NUMERIC)

        meta = client.get_question_meta("test-course", qid)
        assert meta["type"] == QuestionType.NUMERIC.value
        assert meta["options"] is None

    def test_get_question_meta_nonexistent(self, redis_client: redis.Redis) -> None:
        """Test getting metadata for nonexistent question"""
        client = RedisClient(redis_client)

        meta = client.get_question_meta("test-course", "nonexistent-q")
        assert meta is None

    def test_stop_question(self, redis_client: redis.Redis) -> None:
        """Test stopping a question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        # Initially not ended
        meta = client.get_question_meta("test-course", qid)
        assert meta["ended_at"] is None

        # Stop the question
        client.stop_question("test-course", qid)

        # Should have ended_at timestamp
        meta = client.get_question_meta("test-course", qid)
        assert meta["ended_at"] is not None
        assert isinstance(meta["ended_at"], str)

        # Should clear current question
        assert client.get_current_question("test-course") is None

    def test_multiple_questions_for_course(self, redis_client: redis.Redis) -> None:
        """Test creating multiple questions for a course"""
        client = RedisClient(redis_client)

        qid1 = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])
        qid2 = client.create_question("test-course", QuestionType.TF)

        # Both should exist
        assert client.get_question_meta("test-course", qid1) is not None
        assert client.get_question_meta("test-course", qid2) is not None

        # Last one should be current
        assert client.get_current_question("test-course") == qid2


class TestResponseOperations:
    """Test cases for response storage and retrieval"""

    def test_submit_answer_mcq(self, redis_client: redis.Redis) -> None:
        """Test submitting an MCQ answer"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Submit answer
        client.submit_answer("test-course", qid, "A12345678", "A")

        # Retrieve response
        response = client.get_response("test-course", qid, "A12345678")
        assert response is not None
        assert response["resp"] == "A"
        assert "ts" in response

    def test_submit_answer_tf(self, redis_client: redis.Redis) -> None:
        """Test submitting a True/False answer"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.TF)

        client.submit_answer("test-course", qid, "A12345678", True)

        response = client.get_response("test-course", qid, "A12345678")
        assert response["resp"] is True

    def test_submit_answer_numeric(self, redis_client: redis.Redis) -> None:
        """Test submitting a numeric answer"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.NUMERIC)

        client.submit_answer("test-course", qid, "A12345678", 42.5)

        response = client.get_response("test-course", qid, "A12345678")
        assert response["resp"] == 42.5

    def test_update_answer(self, redis_client: redis.Redis) -> None:
        """Test updating an answer (changing from A to B)"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Submit initial answer
        client.submit_answer("test-course", qid, "A12345678", "A")
        response1 = client.get_response("test-course", qid, "A12345678")
        assert response1["resp"] == "A"
        ts1 = response1["ts"]

        # Wait a tiny bit to ensure timestamp changes
        time.sleep(0.01)

        # Update answer
        client.submit_answer("test-course", qid, "A12345678", "B")
        response2 = client.get_response("test-course", qid, "A12345678")
        assert response2["resp"] == "B"
        ts2 = response2["ts"]

        # Timestamp should be updated
        assert ts2 > ts1

    def test_get_response_nonexistent(self, redis_client: redis.Redis) -> None:
        """Test getting response for nonexistent PID"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        response = client.get_response("test-course", qid, "A99999999")
        assert response is None

    def test_get_all_responses(self, redis_client: redis.Redis) -> None:
        """Test getting all responses for a question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Submit multiple answers
        client.submit_answer("test-course", qid, "A11111111", "A")
        client.submit_answer("test-course", qid, "A22222222", "B")
        client.submit_answer("test-course", qid, "A33333333", "A")

        # Get all responses
        responses = client.get_all_responses("test-course", qid)

        assert len(responses) == 3
        assert "A11111111" in responses
        assert "A22222222" in responses
        assert "A33333333" in responses
        assert responses["A11111111"]["resp"] == "A"
        assert responses["A22222222"]["resp"] == "B"
        assert responses["A33333333"]["resp"] == "A"

    def test_get_all_responses_empty(self, redis_client: redis.Redis) -> None:
        """Test getting all responses when none exist"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        responses = client.get_all_responses("test-course", qid)
        assert responses == {}


class TestCountAggregation:
    """Test cases for count aggregation"""

    def test_get_counts_mcq(self, redis_client: redis.Redis) -> None:
        """Test getting counts for MCQ question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Submit various answers
        client.submit_answer("test-course", qid, "A11111111", "A")
        client.submit_answer("test-course", qid, "A22222222", "B")
        client.submit_answer("test-course", qid, "A33333333", "A")
        client.submit_answer("test-course", qid, "A44444444", "C")
        client.submit_answer("test-course", qid, "A55555555", "A")

        # Get counts
        counts = client.get_counts("test-course", qid)

        assert counts["A"] == 3
        assert counts["B"] == 1
        assert counts["C"] == 1

    def test_get_counts_tf(self, redis_client: redis.Redis) -> None:
        """Test getting counts for True/False question"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.TF)

        client.submit_answer("test-course", qid, "A11111111", True)
        client.submit_answer("test-course", qid, "A22222222", False)
        client.submit_answer("test-course", qid, "A33333333", True)

        counts = client.get_counts("test-course", qid)

        assert counts["true"] == 2
        assert counts["false"] == 1

    def test_get_counts_empty(self, redis_client: redis.Redis) -> None:
        """Test getting counts when no answers submitted"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        counts = client.get_counts("test-course", qid)
        assert counts == {}

    def test_counts_update_on_answer_change(self, redis_client: redis.Redis) -> None:
        """Test that counts update correctly when student changes answer"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Initial submissions
        client.submit_answer("test-course", qid, "A11111111", "A")
        client.submit_answer("test-course", qid, "A22222222", "A")

        counts = client.get_counts("test-course", qid)
        assert counts["A"] == 2

        # Student changes answer from A to B
        client.submit_answer("test-course", qid, "A11111111", "B")

        counts = client.get_counts("test-course", qid)
        assert counts["A"] == 1
        assert counts["B"] == 1


class TestAtomicAnswerUpdate:
    """Test cases for atomic answer updates using Lua script"""

    def test_atomic_update_creates_counts(self, redis_client: redis.Redis) -> None:
        """Test that atomic update creates initial counts"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        # First submission should create count
        client.submit_answer("test-course", qid, "A12345678", "A")

        counts = client.get_counts("test-course", qid)
        assert counts["A"] == 1

    def test_atomic_update_increments_new(self, redis_client: redis.Redis) -> None:
        """Test that atomic update increments new answer count"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        client.submit_answer("test-course", qid, "A11111111", "A")
        client.submit_answer("test-course", qid, "A22222222", "A")

        counts = client.get_counts("test-course", qid)
        assert counts["A"] == 2

    def test_atomic_update_decrements_old(self, redis_client: redis.Redis) -> None:
        """Test that atomic update decrements old answer count"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        # Submit initial answer
        client.submit_answer("test-course", qid, "A12345678", "A")
        assert client.get_counts("test-course", qid)["A"] == 1

        # Change answer
        client.submit_answer("test-course", qid, "A12345678", "B")

        counts = client.get_counts("test-course", qid)
        assert counts.get("A", 0) == 0
        assert counts["B"] == 1

    def test_atomic_update_concurrent_safety(self, redis_client: redis.Redis) -> None:
        """Test that concurrent updates maintain consistency"""
        import threading

        client = RedisClient(redis_client)
        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B", "C"])

        # Simulate concurrent submissions
        def submit_answers(start_pid: int, count: int, answer: str) -> None:
            for i in range(count):
                pid = f"A{start_pid + i:08d}"
                client.submit_answer("test-course", qid, pid, answer)

        threads = [
            threading.Thread(target=submit_answers, args=(10000, 20, "A")),
            threading.Thread(target=submit_answers, args=(20000, 20, "B")),
            threading.Thread(target=submit_answers, args=(30000, 20, "C")),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        counts = client.get_counts("test-course", qid)
        assert counts["A"] == 20
        assert counts["B"] == 20
        assert counts["C"] == 20

        # Verify total responses
        responses = client.get_all_responses("test-course", qid)
        assert len(responses) == 60


class TestTTLExpiration:
    """Test cases for TTL expiration"""

    def test_ttl_not_set_on_active_session(self, redis_client: redis.Redis) -> None:
        """Test that TTL is not set on active session keys"""
        client = RedisClient(redis_client)

        client.start_session("test-course")

        session_key = client.session_key("test-course")
        ttl = redis_client.ttl(session_key)

        # -1 means no TTL set
        assert ttl == -1

    def test_ttl_set_on_session_stop(self, redis_client: redis.Redis) -> None:
        """Test that TTL is set when session stops"""
        client = RedisClient(redis_client)

        client.start_session("test-course")
        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])
        client.submit_answer("test-course", qid, "A12345678", "A")

        # Stop with TTL
        client.stop_session("test-course", ttl=60)

        # All keys should have TTL
        keys_to_check = [
            client.session_key("test-course"),
            client.current_qid_key("test-course"),
            client.question_meta_key("test-course", qid),
            client.question_responses_key("test-course", qid),
            client.question_counts_key("test-course", qid),
        ]

        for key in keys_to_check:
            if redis_client.exists(key):
                ttl = redis_client.ttl(key)
                assert 0 < ttl <= 60

    def test_ttl_expiration_cleanup(self, redis_client: redis.Redis) -> None:
        """Test that keys actually expire after TTL"""
        client = RedisClient(redis_client)

        client.start_session("test-course")
        client.stop_session("test-course", ttl=1)

        # Wait for expiration
        time.sleep(2)

        # Session should not be live
        assert not client.is_session_live("test-course")


class TestPubSubEvents:
    """Test cases for pub/sub message publishing"""

    def test_publish_event(self, redis_client: redis.Redis) -> None:
        """Test publishing an event to the channel"""
        client = RedisClient(redis_client)

        # Subscribe to channel
        pubsub = redis_client.pubsub()
        channel = client.events_channel_key("test-course")
        pubsub.subscribe(channel)

        # Skip subscription confirmation message
        msg = pubsub.get_message(timeout=1)
        assert msg is not None
        assert msg["type"] == "subscribe"

        # Publish event
        event_data = {"question_id": "q-123", "type": "mcq"}
        client.publish_event("test-course", EventType.QUESTION_STARTED, event_data)

        # Receive event
        msg = pubsub.get_message(timeout=1)
        assert msg is not None
        assert msg["type"] == "message"
        # Channel can be bytes or string depending on decode_responses setting
        msg_channel = msg["channel"]
        if isinstance(msg_channel, bytes):
            msg_channel = msg_channel.decode()
        assert msg_channel == channel

        data = json.loads(msg["data"])
        assert data["event"] == EventType.QUESTION_STARTED.value
        assert data["data"]["question_id"] == "q-123"

        pubsub.close()

    def test_publish_multiple_events(self, redis_client: redis.Redis) -> None:
        """Test publishing multiple events"""
        client = RedisClient(redis_client)

        pubsub = redis_client.pubsub()
        channel = client.events_channel_key("test-course")
        pubsub.subscribe(channel)
        pubsub.get_message(timeout=1)  # Skip subscribe message

        # Publish multiple events
        client.publish_event("test-course", EventType.SESSION_STARTED, {})
        client.publish_event("test-course", EventType.QUESTION_STARTED, {"question_id": "q-1"})
        client.publish_event("test-course", EventType.QUESTION_STOPPED, {"question_id": "q-1"})

        # Receive all events
        events = []
        for _ in range(3):
            msg = pubsub.get_message(timeout=1)
            if msg and msg["type"] == "message":
                events.append(json.loads(msg["data"]))

        assert len(events) == 3
        assert events[0]["event"] == EventType.SESSION_STARTED.value
        assert events[1]["event"] == EventType.QUESTION_STARTED.value
        assert events[2]["event"] == EventType.QUESTION_STOPPED.value

        pubsub.close()

    def test_multiple_courses_independent_channels(
        self, redis_client: redis.Redis
    ) -> None:
        """Test that different courses have independent pub/sub channels"""
        client = RedisClient(redis_client)

        # Subscribe to course1
        pubsub1 = redis_client.pubsub()
        pubsub1.subscribe(client.events_channel_key("course1"))
        pubsub1.get_message(timeout=1)  # Skip subscribe

        # Subscribe to course2
        pubsub2 = redis_client.pubsub()
        pubsub2.subscribe(client.events_channel_key("course2"))
        pubsub2.get_message(timeout=1)  # Skip subscribe

        # Publish to course1
        client.publish_event("course1", EventType.SESSION_STARTED, {})

        # Only course1 subscriber should receive
        msg1 = pubsub1.get_message(timeout=1)
        msg2 = pubsub2.get_message(timeout=1)

        assert msg1 is not None
        assert msg1["type"] == "message"
        assert msg2 is None

        pubsub1.close()
        pubsub2.close()


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_submit_answer_to_nonexistent_question(
        self, redis_client: redis.Redis
    ) -> None:
        """Test submitting answer to nonexistent question"""
        client = RedisClient(redis_client)

        # Should not raise error, just store the answer
        client.submit_answer("test-course", "nonexistent-q", "A12345678", "A")

        # Answer should be stored
        response = client.get_response("test-course", "nonexistent-q", "A12345678")
        assert response is not None

    def test_stop_nonexistent_question(self, redis_client: redis.Redis) -> None:
        """Test stopping a nonexistent question"""
        client = RedisClient(redis_client)

        # Should not raise error
        client.stop_question("test-course", "nonexistent-q")

    def test_get_counts_for_numeric_question(self, redis_client: redis.Redis) -> None:
        """Test getting counts for numeric question returns all unique values"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.NUMERIC)

        client.submit_answer("test-course", qid, "A11111111", 42)
        client.submit_answer("test-course", qid, "A22222222", 42)
        client.submit_answer("test-course", qid, "A33333333", 43)

        counts = client.get_counts("test-course", qid)

        # Numeric values should be stringified for counting
        assert counts["42"] == 2
        assert counts["43"] == 1

    def test_empty_course_slug(self, redis_client: redis.Redis) -> None:
        """Test operations with empty course slug"""
        client = RedisClient(redis_client)

        # Should work but use empty slug in key
        key = client.session_key("")
        assert key == "course::session:live"

    def test_special_characters_in_answer(self, redis_client: redis.Redis) -> None:
        """Test submitting answers with special characters"""
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.NUMERIC)

        # Submit various formats
        client.submit_answer("test-course", qid, "A11111111", "1/2")
        client.submit_answer("test-course", qid, "A22222222", "0.5")
        client.submit_answer("test-course", qid, "A33333333", "½")

        counts = client.get_counts("test-course", qid)

        # Each should be counted separately (grouping is handled by LLM later)
        assert counts["1/2"] == 1
        assert counts["0.5"] == 1
        assert counts["½"] == 1
