"""
Tests for student Ask routes (Phase 10)

This test file covers:
- Question submission
- Rate limiting (1 question per 10 seconds per PID)
- Length validation (max 1000 chars)
- PID stripping from question text
- Question storage in Redis
- Question retrieval for admin
- Question TTL (expire after 30 minutes)
- Unauthorized submission blocked (no PID cookie)
"""

import time

from fastapi.testclient import TestClient

from app.auth import create_pid_cookie
from app.config import Settings
from app.models import EventType


class TestAskSubmission:
    """Test cases for student question submission"""

    def test_submit_question_success(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test successful question submission"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        # Create PID cookie
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Start session first
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question
        response = client.post(
            "/c/test-course/ask",
            data={"question": "What is the meaning of life?"},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "question_id" in data

    def test_submit_question_strips_pid(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that PIDs are stripped from question text"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question with PID embedded
        question_text = "My PID is A12345678 and I have a question about A98765432"
        response = client.post(
            "/c/test-course/ask",
            data={"question": question_text},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        question_id = data["question_id"]

        # Verify PID was stripped in stored question
        stored_question = redis_client_wrapper.get_question("test-course", question_id)
        assert stored_question is not None
        # PIDs should be replaced with [PID]
        assert "A12345678" not in stored_question["question"]
        assert "A98765432" not in stored_question["question"]
        assert "[PID]" in stored_question["question"]

    def test_submit_question_too_long(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that questions longer than 1000 chars are rejected"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question that's too long (1001 chars)
        question_text = "x" * 1001
        response = client.post(
            "/c/test-course/ask",
            data={"question": question_text},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 422
        data = response.json()
        assert "too long" in data["detail"].lower() or "1000" in data["detail"]

    def test_submit_question_exactly_1000_chars(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that questions with exactly 1000 chars are accepted"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question that's exactly 1000 chars
        question_text = "x" * 1000
        response = client.post(
            "/c/test-course/ask",
            data={"question": question_text},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200

    def test_submit_question_rate_limit(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test rate limiting: 1 question per 10 seconds per PID"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit first question
        response1 = client.post(
            "/c/test-course/ask",
            data={"question": "First question"},
            cookies={"student_session": pid_cookie},
        )
        assert response1.status_code == 200

        # Submit second question immediately (should be rate limited)
        response2 = client.post(
            "/c/test-course/ask",
            data={"question": "Second question"},
            cookies={"student_session": pid_cookie},
        )
        assert response2.status_code == 429  # Too Many Requests
        data = response2.json()
        assert "retry_after" in data
        assert data["retry_after"] > 0

    def test_submit_question_rate_limit_different_pids(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that rate limiting is per-PID (different PIDs don't interfere)"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie1 = create_pid_cookie("A12345678", test_settings.secret_key)
        pid_cookie2 = create_pid_cookie("A87654321", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question from PID 1
        response1 = client.post(
            "/c/test-course/ask",
            data={"question": "Question from PID 1"},
            cookies={"student_session": pid_cookie1},
        )
        assert response1.status_code == 200

        # Submit question from PID 2 immediately (should succeed)
        response2 = client.post(
            "/c/test-course/ask",
            data={"question": "Question from PID 2"},
            cookies={"student_session": pid_cookie2},
        )
        assert response2.status_code == 200

    def test_submit_question_rate_limit_resets(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that rate limit resets after 10 seconds"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit first question
        response1 = client.post(
            "/c/test-course/ask",
            data={"question": "First question"},
            cookies={"student_session": pid_cookie},
        )
        assert response1.status_code == 200

        # Wait for rate limit to reset (10 seconds + small buffer)
        time.sleep(10.5)

        # Submit second question (should succeed)
        response2 = client.post(
            "/c/test-course/ask",
            data={"question": "Second question"},
            cookies={"student_session": pid_cookie},
        )
        assert response2.status_code == 200

    def test_submit_question_no_auth(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that question submission without PID cookie is blocked"""
        response = client.post(
            "/c/test-course/ask",
            data={"question": "Unauthorized question"},
        )

        assert response.status_code == 401

    def test_submit_question_no_session(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that questions can't be submitted when session is not live"""
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Don't start session

        response = client.post(
            "/c/test-course/ask",
            data={"question": "Question without session"},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 400
        data = response.json()
        assert "session" in data["detail"].lower() or "not active" in data["detail"].lower()

    def test_submit_question_stores_timestamp(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that question includes timestamp"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/ask",
            data={"question": "Timestamped question"},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        question_id = data["question_id"]

        # Verify timestamp is stored
        question = redis_client_wrapper.get_question("test-course", question_id)
        assert question is not None
        assert "timestamp" in question
        # Timestamp should be ISO format
        assert "T" in question["timestamp"]


class TestAdminQuestionView:
    """Test cases for admin question viewing"""

    def test_get_questions_empty(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting questions when there are none"""
        from app.redis_client import RedisClient
        from app.auth import create_admin_cookie

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.get(
            "/c/test-course/admin/questions",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_get_questions_with_submissions(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting submitted questions"""
        from app.redis_client import RedisClient
        from app.auth import create_admin_cookie

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit three questions from different PIDs (to avoid rate limiting)
        for i in range(3):
            pid_cookie = create_pid_cookie(f"A1234567{i}", test_settings.secret_key)
            client.post(
                "/c/test-course/ask",
                data={"question": f"Question {i + 1}"},
                cookies={"student_session": pid_cookie},
            )
            time.sleep(0.1)  # Ensure different timestamps

        # Get questions as admin
        response = client.get(
            "/c/test-course/admin/questions",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3

        # Verify structure
        for question in data:
            assert "question_id" in question
            assert "question" in question
            assert "timestamp" in question
            assert "pid" in question

        # Verify sorted by timestamp (newest first)
        timestamps = [q["timestamp"] for q in data]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_get_questions_requires_auth(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that getting questions requires admin auth"""
        response = client.get("/c/test-course/admin/questions")

        assert response.status_code == 403

    def test_question_ttl(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that questions have TTL set"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Submit question
        response = client.post(
            "/c/test-course/ask",
            data={"question": "Question with TTL"},
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        question_id = data["question_id"]

        # Check TTL on question key
        question_key = f"course:test-course:question:{question_id}"
        ttl = redis_client.ttl(question_key)

        # Should have TTL set (30 minutes = 1800 seconds)
        assert 1700 < ttl <= 1800
