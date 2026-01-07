"""
Integration test for question creation with HTMX
This test reproduces the 422 error when creating questions via HTMX
"""

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from app.auth import create_admin_cookie
from app.config import settings
from app.main import app


@pytest.fixture
def client():
    """Create a test client"""
    return TestClient(app)


@pytest.fixture
def redis_client():
    """Create Redis client and clean database before each test"""
    redis_conn = Redis.from_url(settings.redis_url, decode_responses=True)
    redis_conn.flushdb()  # Clean database
    yield redis_conn
    redis_conn.flushdb()  # Clean after test
    redis_conn.close()


@pytest.fixture
def admin_cookie():
    """Create admin cookie for authentication"""
    course = "dsc80-wi25"
    course_config = settings.get_course(course)
    return create_admin_cookie(course, course_config.secret, settings.secret_key)


def test_question_creation_with_json_body(client, redis_client, admin_cookie):
    """Test question creation with proper JSON body - this should work"""
    course = "dsc80-wi25"

    # Start session first
    response = client.post(
        f"/c/{course}/admin/session/start",
        cookies={"admin_session": admin_cookie},
    )
    assert response.status_code == 200

    # Create MCQ question with proper JSON body
    response = client.post(
        f"/c/{course}/admin/question",
        json={"type": "mcq", "options": ["A", "B", "C", "D"]},
        cookies={"admin_session": admin_cookie},
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "question_id" in data


def test_question_creation_with_form_data(client, redis_client, admin_cookie):
    """
    Test question creation with form-encoded data (simulating HTMX hx-vals behavior)
    After the fix, this should work!
    """
    course = "dsc80-wi25"

    # Start session first
    response = client.post(
        f"/c/{course}/admin/session/start",
        cookies={"admin_session": admin_cookie},
    )
    assert response.status_code == 200

    # Create MCQ question with form data (simulating HTMX hx-vals)
    # This now works after the fix!
    response = client.post(
        f"/c/{course}/admin/question",
        data={"type": "mcq", "options": ["A", "B", "C", "D"]},
        cookies={"admin_session": admin_cookie},
    )

    # This should now succeed!
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "question_id" in data
    print(f"\nQuestion created successfully with form data: {data['question_id']}")


def test_question_creation_tf_with_form_data(client, redis_client, admin_cookie):
    """Test True/False question creation with form data - should work now"""
    course = "dsc80-wi25"

    # Start session
    response = client.post(
        f"/c/{course}/admin/session/start",
        cookies={"admin_session": admin_cookie},
    )
    assert response.status_code == 200

    # Create T/F question with form data
    response = client.post(
        f"/c/{course}/admin/question",
        data={"type": "tf"},
        cookies={"admin_session": admin_cookie},
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "question_id" in data


def test_question_creation_numeric_with_form_data(client, redis_client, admin_cookie):
    """Test Numeric question creation with form data - should work now"""
    course = "dsc80-wi25"

    # Start session
    response = client.post(
        f"/c/{course}/admin/session/start",
        cookies={"admin_session": admin_cookie},
    )
    assert response.status_code == 200

    # Create numeric question with form data
    response = client.post(
        f"/c/{course}/admin/question",
        data={"type": "numeric"},
        cookies={"admin_session": admin_cookie},
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert "question_id" in data
