"""
Complete end-to-end integration test

Tests the full flow:
1. Admin starts session
2. Admin creates question
3. Student submits answer
4. Admin stops question
"""

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings
from app.redis_client import RedisClient


class TestCompleteFlow:
    """Test complete question flow from creation to answer submission to stop"""

    def test_complete_mcq_flow(
        self, client: TestClient, test_settings: Settings, redis_client: Redis
    ) -> None:
        """
        Test complete MCQ flow:
        1. Admin starts session
        2. Admin creates MCQ question
        3. Student submits answer
        4. Admin stops question
        """
        # Setup
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )
        student_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # 1. Admin starts session
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200
        print("\n✓ Session started")

        # 2. Admin creates MCQ question (using form data)
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        )
        assert response.status_code == 200, f"Question creation failed: {response.text}"
        data = response.json()
        question_id = data["question_id"]
        print(f"✓ Question created: {question_id}")

        # 3. Student submits answer (using form data)
        response = client.post(
            "/c/test-course/answer",
            cookies={"student_session": student_cookie},
            data={"question_id": question_id, "response": "A"},
        )
        assert response.status_code == 200, f"Answer submission failed: {response.text}"
        data = response.json()
        assert data["status"] == "submitted"
        assert data["counts"]["A"] == 1
        print("✓ Student answer submitted")

        # 4. Admin stops question
        response = client.post(
            f"/c/test-course/admin/question/{question_id}/stop",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200, f"Stop question failed: {response.text}"
        print("✓ Question stopped")

        # Verify question is marked as ended
        redis_wrapper = RedisClient(redis_client)
        meta = redis_wrapper.get_question_meta("test-course", question_id)
        assert meta is not None
        assert meta["ended_at"] is not None
        print("✓ Question marked as ended in Redis")

        # Verify student can no longer submit answer after question stopped
        response = client.post(
            "/c/test-course/answer",
            cookies={"student_session": student_cookie},
            data={"question_id": question_id, "response": "B"},
        )
        assert response.status_code == 400
        assert "ended" in response.json()["detail"].lower()
        print("✓ Answer submission after stop correctly rejected")

    def test_complete_tf_flow(
        self, client: TestClient, test_settings: Settings, redis_client: Redis
    ) -> None:
        """
        Test complete True/False flow
        """
        # Setup
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )
        student_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Start session
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200

        # Create T/F question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "tf"},
        )
        assert response.status_code == 200
        question_id = response.json()["question_id"]

        # Student submits answer (boolean value as string from form data)
        response = client.post(
            "/c/test-course/answer",
            cookies={"student_session": student_cookie},
            data={"question_id": question_id, "response": "true"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "submitted"
        # Boolean true is converted to lowercase "true" for counting in Redis
        assert "true" in data["counts"]

        # Stop question
        response = client.post(
            f"/c/test-course/admin/question/{question_id}/stop",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200

    def test_complete_numeric_flow(
        self, client: TestClient, test_settings: Settings, redis_client: Redis
    ) -> None:
        """
        Test complete numeric flow
        """
        # Setup
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )
        student_cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Start session
        response = client.post(
            "/c/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200

        # Create numeric question
        response = client.post(
            "/c/test-course/admin/question",
            cookies={"admin_session": admin_cookie},
            data={"type": "numeric"},
        )
        assert response.status_code == 200
        question_id = response.json()["question_id"]

        # Student submits numeric answer
        response = client.post(
            "/c/test-course/answer",
            cookies={"student_session": student_cookie},
            data={"question_id": question_id, "response": "42"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "submitted"
        assert "42" in data["counts"]

        # Stop question
        response = client.post(
            f"/c/test-course/admin/question/{question_id}/stop",
            cookies={"admin_session": admin_cookie},
        )
        assert response.status_code == 200
