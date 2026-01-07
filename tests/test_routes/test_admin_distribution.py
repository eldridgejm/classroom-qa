"""
Tests for admin distribution display routes (Phase 8)

This test file covers:
- GET endpoint returns current counts
- Counts format for MCQ (dict of option → count)
- Counts format for T/F (true/false counts)
- Counts format for Numeric (all values with counts)
- Empty counts when no responses
- Counts update after student answers
- Unauthorized access blocked
- Distribution for non-existent question returns 404
- Distribution only available for current question
"""

from fastapi.testclient import TestClient

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings
from app.models import QuestionType


class TestDistributionEndpoint:
    """Test cases for distribution display endpoint"""

    def test_get_distribution_mcq(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution for MCQ question"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create MCQ question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C", "D"]
        )

        # Submit some answers
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")
        redis_client_wrapper.submit_answer("test-course", qid, "A12345679", "B")
        redis_client_wrapper.submit_answer("test-course", qid, "A12345680", "A")
        redis_client_wrapper.submit_answer("test-course", qid, "A12345681", "C")

        # Get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["question_id"] == qid
        assert data["type"] == "mcq"
        assert data["total"] == 4
        assert data["counts"] == {"A": 2, "B": 1, "C": 1, "D": 0}
        assert data["percentages"]["A"] == 50.0
        assert data["percentages"]["B"] == 25.0
        assert data["percentages"]["C"] == 25.0
        assert data["percentages"]["D"] == 0.0

    def test_get_distribution_tf(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution for True/False question"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create T/F question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question("test-course", QuestionType.TF)

        # Submit answers
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", True)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345679", False)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345680", True)

        # Get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["question_id"] == qid
        assert data["type"] == "tf"
        assert data["total"] == 3
        # Counts should have "true" and "false" as string keys
        assert data["counts"]["true"] == 2
        assert data["counts"]["false"] == 1
        assert abs(data["percentages"]["true"] - 66.67) < 0.01
        assert abs(data["percentages"]["false"] - 33.33) < 0.01

    def test_get_distribution_numeric(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution for Numeric question"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create Numeric question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question("test-course", QuestionType.NUMERIC)

        # Submit numeric answers
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", 42)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345679", 3.14)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345680", 42)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345681", 100)

        # Get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["question_id"] == qid
        assert data["type"] == "numeric"
        assert data["total"] == 4
        # Counts should have numeric values as string keys
        assert data["counts"]["42"] == 2
        assert data["counts"]["3.14"] == 1
        assert data["counts"]["100"] == 1
        assert data["percentages"]["42"] == 50.0
        assert data["percentages"]["3.14"] == 25.0
        assert data["percentages"]["100"] == 25.0

    def test_get_distribution_empty(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution when no responses yet"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create MCQ question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C"]
        )

        # Get distribution without any answers
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert data["question_id"] == qid
        assert data["type"] == "mcq"
        assert data["total"] == 0
        assert data["counts"] == {"A": 0, "B": 0, "C": 0}
        assert data["percentages"] == {"A": 0.0, "B": 0.0, "C": 0.0}

    def test_get_distribution_no_active_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution when no active question"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session but don't create any question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # Get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "no active question" in data["detail"].lower()

    def test_get_distribution_unauthorized(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution without admin auth"""
        from app.redis_client import RedisClient

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")
        redis_client_wrapper.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        # Try to get distribution without admin cookie
        response = client.get("/c/test-course/admin/distribution")

        assert response.status_code == 403

    def test_get_distribution_invalid_admin_cookie(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test getting distribution with invalid admin cookie"""
        from app.redis_client import RedisClient

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")
        redis_client_wrapper.create_question("test-course", QuestionType.MCQ, ["A", "B"])

        # Try with invalid cookie
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": "invalid-cookie"},
        )

        assert response.status_code == 403

    def test_get_distribution_counts_update_on_answer(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that distribution updates when student changes answer"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create MCQ question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C"]
        )

        # Submit initial answer
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")

        # Get initial distribution
        response1 = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response1.status_code == 200
        data1 = response1.json()
        assert data1["counts"]["A"] == 1
        assert data1["counts"]["B"] == 0

        # Change answer
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "B")

        # Get updated distribution
        response2 = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["counts"]["A"] == 0
        assert data2["counts"]["B"] == 1

    def test_get_distribution_invalid_course(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test getting distribution for non-existent course"""
        # Try with a course that doesn't exist
        # Note: Auth fails first (403) before course check (404)
        response = client.get(
            "/c/nonexistent-course/admin/distribution",
            cookies={"admin_session": "some-cookie"},
        )

        assert response.status_code == 403

    def test_get_distribution_session_not_started(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test getting distribution when session not started"""
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Don't start session, just try to get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        # Should return 404 since there's no active question (session not started)
        assert response.status_code == 404

    def test_get_distribution_with_options(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that distribution includes options for MCQ"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create MCQ question with custom options
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        options = ["Option A", "Option B", "Option C", "Option D"]
        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, options
        )

        # Get distribution
        response = client.get(
            "/c/test-course/admin/distribution",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        # Should include options
        assert "options" in data
        assert data["options"] == options
