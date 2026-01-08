"""
Tests for student answer submission routes (Phase 6)

This test file covers:
- MCQ answer submission
- T/F answer submission
- Numeric answer submission
- Answer updates (changing answers)
- Count increment/decrement
- Timestamp updates
- Answer without active question rejected
- Answer to non-existent question rejected
- Unauthorized submission blocked (no PID cookie)
- Concurrent answer updates maintain consistency
- Invalid answer values rejected
"""

import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from app.auth import create_pid_cookie
from app.config import Settings
from app.models import QuestionType
from app.redis_client import RedisClient


class TestMCQAnswerSubmission:
    """Test cases for MCQ answer submission"""

    def test_submit_mcq_answer(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting an MCQ answer"""
        # Setup: create session and question
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C", "D"]
        )

        # Create PID cookie
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit answer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "submitted"

        # Verify answer stored in Redis
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored is not None
        assert stored["resp"] == "A"

        # Verify count incremented
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["A"] == 1

    def test_submit_multiple_mcq_answers(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test multiple students submitting MCQ answers"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C", "D"]
        )

        # Submit answers from different students
        students = [
            ("A12345678", "A"),
            ("A87654321", "B"),
            ("A11111111", "A"),
            ("A22222222", "C"),
            ("A33333333", "A"),
        ]

        for pid, answer in students:
            pid_cookie = create_pid_cookie(pid, test_settings.secret_key)
            response = client.post(
                "/test-course/answer",
                cookies={"student_session": pid_cookie},
                data={"question_id": qid, "response": answer},
            )
            assert response.status_code == 200

        # Verify counts
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["A"] == 3
        assert counts["B"] == 1
        assert counts["C"] == 1
        assert counts.get("D", 0) == 0

    def test_change_mcq_answer(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test changing an MCQ answer (A to B)"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C", "D"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit first answer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )
        assert response.status_code == 200

        # Verify initial count
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["A"] == 1

        # Change answer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "B"},
        )
        assert response.status_code == 200

        # Verify counts updated correctly
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts.get("A", 0) == 0  # A decremented
        assert counts["B"] == 1  # B incremented

        # Verify stored answer
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] == "B"

    def test_mcq_invalid_option(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting invalid MCQ option"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C", "D"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit invalid option
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "E"},
        )

        assert response.status_code == 400
        assert "invalid" in response.json()["detail"].lower()


class TestTrueFalseAnswerSubmission:
    """Test cases for True/False answer submission"""

    def test_submit_tf_answer_true(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting True answer"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.TF)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit True
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": True},
        )

        assert response.status_code == 200

        # Verify answer stored
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] is True

        # Verify count
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["true"] == 1

    def test_submit_tf_answer_false(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting False answer"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.TF)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit False
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": False},
        )

        assert response.status_code == 200

        # Verify answer stored
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] is False

        # Verify count
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["false"] == 1

    def test_change_tf_answer(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test changing T/F answer"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.TF)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit True
        client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": True},
        )

        # Change to False
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": False},
        )

        assert response.status_code == 200

        # Verify counts
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts.get("true", 0) == 0
        assert counts["false"] == 1


class TestNumericAnswerSubmission:
    """Test cases for numeric answer submission"""

    def test_submit_numeric_answer_int(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting numeric answer (integer)"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.NUMERIC)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit integer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": 42},
        )

        assert response.status_code == 200

        # Verify answer stored
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] == 42

    def test_submit_numeric_answer_float(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting numeric answer (float)"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.NUMERIC)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit float
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": 3.14},
        )

        assert response.status_code == 200

        # Verify answer stored
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] == 3.14

    def test_submit_numeric_answer_string(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting numeric answer as string"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.NUMERIC)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit string (like "1/2")
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "1/2"},
        )

        assert response.status_code == 200

        # Verify answer stored
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored["resp"] == "1/2"


class TestAnswerValidation:
    """Test cases for answer validation"""

    def test_answer_without_active_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting answer when no question is active"""
        # Setup: session but no question
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit answer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": "q-nonexistent", "response": "A"},
        )

        assert response.status_code == 400
        assert "no active question" in response.json()["detail"].lower() or \
               "not found" in response.json()["detail"].lower()

    def test_answer_to_stopped_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting answer to a stopped question"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        # Stop the question
        redis_wrapper.stop_question("test-course", qid)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit answer
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )

        assert response.status_code == 400
        assert "not active" in response.json()["detail"].lower() or \
               "ended" in response.json()["detail"].lower()

    def test_answer_without_pid_cookie(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting answer without PID cookie"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        # Try to submit without cookie
        response = client.post(
            "/test-course/answer",
            data={"question_id": qid, "response": "A"},
        )

        assert response.status_code in [401, 403]

    def test_answer_type_mismatch_mcq_with_boolean(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting boolean to MCQ question"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit boolean to MCQ
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": True},
        )

        assert response.status_code == 400
        detail_lower = response.json()["detail"].lower()
        assert any(word in detail_lower for word in ["type", "invalid", "require", "string"])

    def test_answer_type_mismatch_tf_with_string(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting string to T/F question"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question("test-course", QuestionType.TF)

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit string to T/F
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "yes"},
        )

        assert response.status_code == 400


class TestTimestampUpdates:
    """Test cases for timestamp tracking"""

    def test_timestamp_recorded(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that timestamp is recorded with answer"""
        from datetime import datetime, UTC

        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit answer
        before_time = datetime.now(UTC)
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )
        after_time = datetime.now(UTC)

        assert response.status_code == 200

        # Verify timestamp
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert "ts" in stored
        ts_str = stored["ts"]
        ts = datetime.fromisoformat(ts_str)
        assert before_time <= ts <= after_time

    def test_timestamp_updates_on_change(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that timestamp updates when answer changes"""
        from datetime import datetime

        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Submit first answer
        client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )

        stored1 = redis_wrapper.get_response("test-course", qid, "A12345678")
        ts1 = datetime.fromisoformat(stored1["ts"])

        # Wait a bit
        time.sleep(0.1)

        # Change answer
        client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "B"},
        )

        stored2 = redis_wrapper.get_response("test-course", qid, "A12345678")
        ts2 = datetime.fromisoformat(stored2["ts"])

        # Timestamp should be updated
        assert ts2 > ts1


class TestConcurrentAnswers:
    """Test cases for concurrent answer submissions"""

    def test_concurrent_different_students(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test concurrent submissions from different students"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C"]
        )

        # Define submission function
        def submit_answer(pid: str, answer: str) -> None:
            pid_cookie = create_pid_cookie(pid, test_settings.secret_key)
            client.post(
                "/test-course/answer",
                cookies={"student_session": pid_cookie},
                data={"question_id": qid, "response": answer},
            )

        # Submit concurrently
        threads = []
        students = [
            ("A11111111", "A"),
            ("A22222222", "B"),
            ("A33333333", "A"),
            ("A44444444", "C"),
            ("A55555555", "A"),
        ]

        for pid, answer in students:
            thread = threading.Thread(target=submit_answer, args=(pid, answer))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify all answers recorded correctly
        counts = redis_wrapper.get_counts("test-course", qid)
        assert counts["A"] == 3
        assert counts["B"] == 1
        assert counts["C"] == 1

    def test_concurrent_same_student_changes(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test concurrent answer changes from same student"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B", "C", "D"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Define submission function
        def submit_answer(answer: str) -> None:
            client.post(
                "/test-course/answer",
                cookies={"student_session": pid_cookie},
                data={"question_id": qid, "response": answer},
            )

        # Submit multiple changes concurrently
        threads = []
        answers = ["A", "B", "C", "D", "A"]

        for answer in answers:
            thread = threading.Thread(target=submit_answer, args=(answer,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        # Verify consistency: exactly one answer stored, total count = 1
        stored = redis_wrapper.get_response("test-course", qid, "A12345678")
        assert stored is not None
        final_answer = stored["resp"]

        counts = redis_wrapper.get_counts("test-course", qid)
        total_count = sum(counts.values())
        assert total_count == 1
        assert counts[final_answer] == 1


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_empty_response(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting empty response"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit empty string (Pydantic validation catches this)
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": ""},
        )

        assert response.status_code in [400, 422]  # 422 for Pydantic validation error

    def test_null_response(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting null response"""
        # Setup
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try to submit null (Pydantic validation catches this)
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": None},
        )

        assert response.status_code in [400, 422]  # 422 for Pydantic validation error

    def test_invalid_question_id_format(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test submitting to invalid question ID"""
        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")

        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Try with malformed question ID
        response = client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": "invalid", "response": "A"},
        )

        assert response.status_code == 400

    def test_nonexistent_course(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test submitting to nonexistent course"""
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        response = client.post(
            "/nonexistent-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": "q-123", "response": "A"},
        )

        assert response.status_code in [400, 404]


class TestResultsEndpoint:
    """Tests for student results viewing after instructors share."""

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

    def test_student_can_view_results_after_share(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Students receive counts and their answer after results are shared."""

        redis_wrapper, qid = self._start_session_with_question(redis_client)
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)
        other_cookie = create_pid_cookie("A00000000", test_settings.secret_key)

        client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )
        client.post(
            "/test-course/answer",
            cookies={"student_session": other_cookie},
            data={"question_id": qid, "response": "B"},
        )

        redis_wrapper.stop_question("test-course", qid)
        assert redis_wrapper.mark_results_shared("test-course", qid)

        response = client.get(
            f"/test-course/results/{qid}",
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["question_id"] == qid
        assert data["counts"]["A"] == 1
        assert data["counts"]["B"] == 1
        assert data["your_answer"] == "A"

    def test_results_not_available_until_shared(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Endpoint returns 404 until instructors choose to share."""

        redis_wrapper, qid = self._start_session_with_question(redis_client)
        pid_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        client.post(
            "/test-course/answer",
            cookies={"student_session": pid_cookie},
            data={"question_id": qid, "response": "A"},
        )

        redis_wrapper.stop_question("test-course", qid)

        response = client.get(
            f"/test-course/results/{qid}",
            cookies={"student_session": pid_cookie},
        )

        assert response.status_code == 404

    def test_results_shows_null_when_student_did_not_answer(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Students who skipped the question see null for their answer."""

        redis_wrapper, qid = self._start_session_with_question(redis_client)
        answering_cookie = create_pid_cookie("A12345678", test_settings.secret_key)
        viewing_cookie = create_pid_cookie("A99999999", test_settings.secret_key)

        client.post(
            "/test-course/answer",
            cookies={"student_session": answering_cookie},
            data={"question_id": qid, "response": "B"},
        )

        redis_wrapper.stop_question("test-course", qid)
        assert redis_wrapper.mark_results_shared("test-course", qid)

        response = client.get(
            f"/test-course/results/{qid}",
            cookies={"student_session": viewing_cookie},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["your_answer"] is None

    def test_results_endpoint_requires_auth(
        self, client: TestClient, redis_client
    ) -> None:
        """PID authentication is required to view shared results."""

        redis_wrapper = RedisClient(redis_client)
        redis_wrapper.start_session("test-course")
        qid = redis_wrapper.create_question(
            "test-course", QuestionType.MCQ, options=["A", "B"]
        )
        redis_wrapper.stop_question("test-course", qid)
        redis_wrapper.mark_results_shared("test-course", qid)

        response = client.get(f"/test-course/results/{qid}")
        assert response.status_code in [401, 403]
