"""
Tests for admin question lifecycle routes (Phase 5)

This test file covers:
- Session start/stop
- Question creation (MCQ, T/F, Numeric)
- Question appears as current
- Question stop
- Multiple questions lifecycle
- Can't create question when session inactive
- Unauthorized access blocked
- Invalid question types rejected
- Missing options for MCQ
"""

from fastapi.testclient import TestClient

from app.auth import create_admin_cookie
from app.config import Settings
from app.models import QuestionType
from app.redis_client import RedisClient


class TestSessionManagement:
    """Test cases for session start/stop"""

    def test_session_start(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test starting a session"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"

        # Verify session is live in Redis
        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        assert redis_client_wrapper.is_session_live("test-course")

    def test_session_start_requires_auth(self, client: TestClient) -> None:
        """Test that starting session requires authentication"""
        response = client.post("/c/test-course/admin/session/start")

        assert response.status_code in [401, 403]

    def test_session_stop(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test stopping a session"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session first
        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/session/stop",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

        # Verify session is no longer live
        assert not redis_client_wrapper.is_session_live("test-course")

    def test_session_stop_requires_auth(self, client: TestClient) -> None:
        """Test that stopping session requires authentication"""
        response = client.post("/c/test-course/admin/session/stop")

        assert response.status_code in [401, 403]

    def test_session_lifecycle(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test complete session start/stop lifecycle"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)

        # Initially not live
        assert not redis_client_wrapper.is_session_live("test-course")

        # Start session
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": cookie},
        )
        assert response.status_code == 200
        assert redis_client_wrapper.is_session_live("test-course")

        # Stop session
        response = client.post(
            "/c/test-course/admin/session/stop",
            cookies={"admin_session": cookie},
        )
        assert response.status_code == 200
        assert not redis_client_wrapper.is_session_live("test-course")


class TestQuestionCreation:
    """Test cases for question creation"""

    def test_create_mcq_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test creating an MCQ question"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session first
        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={
                "type": "mcq",
                "options": ["A", "B", "C", "D"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "question_id" in data
        assert data["question_id"].startswith("q-")

        # Verify question exists in Redis
        qid = data["question_id"]
        meta = redis_client_wrapper.get_question_meta("test-course", qid)
        assert meta is not None
        assert meta["type"] == QuestionType.MCQ.value
        assert meta["options"] == ["A", "B", "C", "D"]

    def test_create_tf_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test creating a True/False question"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "question_id" in data

        # Verify question
        qid = data["question_id"]
        meta = redis_client_wrapper.get_question_meta("test-course", qid)
        assert meta["type"] == QuestionType.TF.value
        assert meta["options"] is None

    def test_create_numeric_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test creating a numeric question"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "numeric"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "question_id" in data

        # Verify question
        qid = data["question_id"]
        meta = redis_client_wrapper.get_question_meta("test-course", qid)
        assert meta["type"] == QuestionType.NUMERIC.value

    def test_create_question_requires_auth(self, client: TestClient) -> None:
        """Test that creating question requires authentication"""
        response = client.post(
            "/c/test-course/admin/question",
            data={"type": "mcq", "options": ["A", "B"]},
        )

        assert response.status_code in [401, 403]

    def test_create_question_without_active_session(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that creating question requires active session"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Don't start session
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq", "options": ["A", "B"]},
        )

        assert response.status_code == 400
        assert "session" in response.json()["detail"].lower()

    def test_create_mcq_without_options(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that MCQ requires options"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq"},
        )

        assert response.status_code == 422

    def test_create_question_invalid_type(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that invalid question type is rejected"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "invalid_type"},
        )

        assert response.status_code == 422


class TestCurrentQuestion:
    """Test cases for current question tracking"""

    def test_question_appears_as_current(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that created question becomes current"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )

        assert response.status_code == 200
        qid = response.json()["question_id"]

        # Verify it's the current question
        current_qid = redis_client_wrapper.get_current_question("test-course")
        assert current_qid == qid

    def test_multiple_questions_update_current(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that creating new question updates current"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Create first question
        response1 = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )
        qid1 = response1.json()["question_id"]

        # Create second question
        response2 = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq", "options": ["A", "B"]},
        )
        qid2 = response2.json()["question_id"]

        # Current should be the second question
        current_qid = redis_client_wrapper.get_current_question("test-course")
        assert current_qid == qid2
        assert current_qid != qid1


class TestQuestionStop:
    """Test cases for stopping questions"""

    def test_stop_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test stopping a question"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Create question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )
        qid = response.json()["question_id"]

        # Stop question
        response = client.post(
            f"/c/test-course/admin/question/{qid}/stop",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "stopped"

        # Verify question is stopped in Redis
        meta = redis_client_wrapper.get_question_meta("test-course", qid)
        assert meta["ended_at"] is not None

        # Verify it's no longer current
        current_qid = redis_client_wrapper.get_current_question("test-course")
        assert current_qid is None

    def test_stop_question_requires_auth(self, client: TestClient) -> None:
        """Test that stopping question requires authentication"""
        response = client.post("/c/test-course/admin/question/q-123/stop")

        assert response.status_code in [401, 403]

    def test_stop_nonexistent_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test stopping a nonexistent question"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question/nonexistent-q/stop",
            cookies={"admin_session": cookie},
        )

        # Should handle gracefully
        assert response.status_code in [200, 404]


class TestMultipleQuestionsLifecycle:
    """Test cases for multiple questions in a session"""

    def test_multiple_questions_lifecycle(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test creating and stopping multiple questions"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Create first question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        )
        qid1 = response.json()["question_id"]
        assert redis_client_wrapper.get_current_question("test-course") == qid1

        # Stop first question
        client.post(
            f"/c/test-course/admin/question/{qid1}/stop",
            cookies={"admin_session": cookie},
        )
        assert redis_client_wrapper.get_current_question("test-course") is None

        # Create second question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )
        qid2 = response.json()["question_id"]
        assert redis_client_wrapper.get_current_question("test-course") == qid2

        # Stop second question
        client.post(
            f"/c/test-course/admin/question/{qid2}/stop",
            cookies={"admin_session": cookie},
        )
        assert redis_client_wrapper.get_current_question("test-course") is None

        # Both questions should exist in Redis
        assert redis_client_wrapper.get_question_meta("test-course", qid1) is not None
        assert redis_client_wrapper.get_question_meta("test-course", qid2) is not None

    def test_session_with_multiple_question_types(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test session with MCQ, T/F, and Numeric questions"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Create MCQ
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq", "options": ["A", "B", "C"]},
        )
        assert response.status_code == 200

        # Create T/F
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )
        assert response.status_code == 200

        # Create Numeric
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "numeric"},
        )
        assert response.status_code == 200
        qid_numeric = response.json()["question_id"]

        # Last one should be current
        assert redis_client_wrapper.get_current_question("test-course") == qid_numeric


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_create_question_for_nonexistent_course(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test creating question for nonexistent course"""
        # Even with valid cookie, course must exist
        response = client.post(
            "/c/nonexistent-course/admin/question",
            data={"type": "tf"},
        )

        assert response.status_code in [401, 403, 404]

    def test_stop_session_with_active_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test stopping session with active question still running"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Create question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "tf"},
        )
        qid = response.json()["question_id"]

        # Stop session (should handle active question)
        response = client.post(
            "/c/test-course/admin/session/stop",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        # Session should be stopped
        assert not redis_client_wrapper.is_session_live("test-course")

    def test_double_start_session(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test starting session that's already started"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": cookie},
        )
        assert response.status_code == 200

        # Start again
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": cookie},
        )

        # Should handle gracefully
        assert response.status_code == 200

    def test_mcq_with_empty_options_list(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test MCQ with empty options list"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        from app.redis_client import RedisClient
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": cookie},
            data={"type": "mcq", "options": []},
        )

        # Should reject empty options
        assert response.status_code == 422


class TestShareResults:
    """Test cases for sharing results with students"""

    @staticmethod
    def _start_session_with_question(redis_client) -> tuple[RedisClient, str]:
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course",
            QuestionType.MCQ,
            options=["A", "B"],
        )
        return redis_wrapper, qid

    def test_share_results_after_stop(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Instructor can share results after stopping a question."""

        course = test_settings.get_course("test-course")
        assert course is not None
        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_wrapper, qid = self._start_session_with_question(redis_client)

        stop_response = client.post(
            f"/c/test-course/admin/question/{qid}/stop",
            cookies={"admin_session": admin_cookie},
        )
        assert stop_response.status_code == 200

        response = client.post(
            f"/c/test-course/admin/question/{qid}/share-results",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "shared"
        assert data["question_id"] == qid

        meta = redis_wrapper.get_question_meta("test-course", qid)
        assert meta is not None
        assert meta.get("results_shared") is True
        assert meta.get("results_shared_at") is not None

    def test_share_results_requires_stop(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Sharing results before stopping returns an error."""

        course = test_settings.get_course("test-course")
        assert course is not None
        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        _, qid = self._start_session_with_question(redis_client)

        response = client.post(
            f"/c/test-course/admin/question/{qid}/share-results",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 400
        assert "stopped" in response.json()["detail"].lower()

    def test_share_results_idempotent(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Sharing twice returns already_shared on the second attempt."""

        course = test_settings.get_course("test-course")
        assert course is not None
        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        _, qid = self._start_session_with_question(redis_client)

        client.post(
            f"/c/test-course/admin/question/{qid}/stop",
            cookies={"admin_session": admin_cookie},
        )

        first = client.post(
            f"/c/test-course/admin/question/{qid}/share-results",
            cookies={"admin_session": admin_cookie},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "shared"

        second = client.post(
            f"/c/test-course/admin/question/{qid}/share-results",
            cookies={"admin_session": admin_cookie},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "already_shared"

    def test_share_results_requires_auth(self, client: TestClient) -> None:
        """Sharing results without admin auth is rejected."""

        response = client.post(
            "/c/test-course/admin/question/q-any/share-results",
        )
        assert response.status_code in [401, 403]
