"""
Tests for admin export routes (Phase 9)

This test file covers:
- Export JSON format matches spec
- All questions included in export
- Latest answer per student included
- Timestamps are ISO 8601 format
- Auth required
- Export available after session end (within 30 min)
- Export unavailable after TTL (mock time)
- Export with no responses
- Export streaming (large dataset)
"""

import json
import time

from fastapi.testclient import TestClient

from app.auth import create_admin_cookie
from app.config import Settings
from app.models import QuestionType


class TestExportEndpoint:
    """Test cases for export endpoint"""

    def test_export_basic_format(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test basic export format with one question"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C", "D"]
        )

        # Submit some answers
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")
        redis_client_wrapper.submit_answer("test-course", qid, "A12345679", "B")

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1

        question_data = data[0]
        assert question_data["question_id"] == qid
        assert question_data["type"] == "mcq"
        assert question_data["options"] == ["A", "B", "C", "D"]
        assert "responses" in question_data
        assert len(question_data["responses"]) == 2

    def test_export_all_questions(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that all questions are included in export"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create multiple questions
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid1 = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid1, "A12345678", "A")
        redis_client_wrapper.stop_question("test-course", qid1)

        qid2 = redis_client_wrapper.create_question("test-course", QuestionType.TF)
        redis_client_wrapper.submit_answer("test-course", qid2, "A12345678", True)
        redis_client_wrapper.stop_question("test-course", qid2)

        qid3 = redis_client_wrapper.create_question(
            "test-course", QuestionType.NUMERIC
        )
        redis_client_wrapper.submit_answer("test-course", qid3, "A12345678", 42)

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 3
        question_ids = [q["question_id"] for q in data]
        assert qid1 in question_ids
        assert qid2 in question_ids
        assert qid3 in question_ids

    def test_export_response_format(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test response format includes timestamp and answer"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        responses = data[0]["responses"]
        assert "A12345678" in responses

        student_response = responses["A12345678"]
        assert "timestamp" in student_response
        assert "response" in student_response
        assert student_response["response"] == "A"

        # Verify timestamp is ISO 8601 format
        timestamp = student_response["timestamp"]
        assert "T" in timestamp
        assert timestamp.endswith("Z") or "+" in timestamp or "-" in timestamp[-6:]

    def test_export_latest_answer_only(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that only the latest answer per student is included"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C"]
        )

        # Student changes answer multiple times
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")
        time.sleep(0.01)  # Ensure different timestamps
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "B")
        time.sleep(0.01)
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "C")

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        responses = data[0]["responses"]
        # Should only have one entry per student
        assert len(responses) == 1
        assert responses["A12345678"]["response"] == "C"

    def test_export_no_responses(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test export with question but no responses"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create question without answers
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert data[0]["question_id"] == qid
        assert data[0]["responses"] == {}

    def test_export_no_session(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test export when no session exists"""
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Export without starting session
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_export_unauthorized(self, client: TestClient) -> None:
        """Test export requires admin auth"""
        response = client.get("/test-course/admin/export")

        assert response.status_code == 403

    def test_export_invalid_course(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test export for non-existent course"""
        response = client.get(
            "/nonexistent-course/admin/export",
            cookies={"admin_session": "some-cookie"},
        )

        assert response.status_code == 403

    def test_export_different_question_types(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test export handles all question types correctly"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create different question types
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        # MCQ
        qid1 = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C"]
        )
        redis_client_wrapper.submit_answer("test-course", qid1, "A12345678", "B")

        # T/F
        qid2 = redis_client_wrapper.create_question("test-course", QuestionType.TF)
        redis_client_wrapper.submit_answer("test-course", qid2, "A12345679", False)

        # Numeric
        qid3 = redis_client_wrapper.create_question(
            "test-course", QuestionType.NUMERIC
        )
        redis_client_wrapper.submit_answer("test-course", qid3, "A12345680", 3.14)

        # Export
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 3

        # Find each question in export
        mcq = next(q for q in data if q["question_id"] == qid1)
        tf = next(q for q in data if q["question_id"] == qid2)
        numeric = next(q for q in data if q["question_id"] == qid3)

        assert mcq["type"] == "mcq"
        assert mcq["options"] == ["A", "B", "C"]
        assert mcq["responses"]["A12345678"]["response"] == "B"

        assert tf["type"] == "tf"
        assert tf["responses"]["A12345679"]["response"] is False

        assert numeric["type"] == "numeric"
        assert numeric["responses"]["A12345680"]["response"] == 3.14


class TestSessionTTL:
    """Test cases for session archiving on stop"""

    def test_session_stop_applies_ttl(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that stopping session archives data and clears current session"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session and create question
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")

        # Get keys before stopping
        session_key = redis_client_wrapper.session_key("test-course")
        current_qid_key = redis_client_wrapper.current_qid_key("test-course")
        question_meta_key = redis_client_wrapper.question_meta_key("test-course", qid)
        responses_key = redis_client_wrapper.question_responses_key("test-course", qid)
        counts_key = redis_client_wrapper.question_counts_key("test-course", qid)

        # Verify keys exist and have no TTL (-1)
        assert redis_client.ttl(session_key) == -1
        assert redis_client.ttl(current_qid_key) == -1
        assert redis_client.ttl(question_meta_key) == -1
        assert redis_client.ttl(responses_key) == -1
        assert redis_client.ttl(counts_key) == -1

        # Stop session
        response = client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200

        # Verify current session keys are deleted (TTL = -2 means key doesn't exist)
        assert redis_client.ttl(session_key) == -2
        assert redis_client.ttl(current_qid_key) == -2
        assert redis_client.ttl(question_meta_key) == -2
        assert redis_client.ttl(responses_key) == -2
        assert redis_client.ttl(counts_key) == -2

        # Verify archive was created with TTL
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1
        session_id = archives[0]["session_id"]
        archive_key = redis_client_wrapper.archive_key("test-course", session_id)
        assert 86390 < redis_client.ttl(archive_key) <= 86400

    def test_export_available_after_session_stop(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that archived data is available after session stops"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start session, create question, submit answer
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")

        # Stop session
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Current session export should be empty
        response = client.get(
            "/test-course/admin/export",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 0  # Current session is cleared

        # But archived session should be available
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1
        session_id = archives[0]["session_id"]

        # Download archived session
        response = client.get(
            f"/test-course/admin/archives/{session_id}",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        archive_data = response.json()
        assert len(archive_data["questions"]) == 1
        assert archive_data["questions"][0]["question_id"] == qid
        assert "A12345678" in archive_data["questions"][0]["responses"]
