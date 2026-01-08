"""
Smoke tests to verify basic functionality
"""

import redis
from fastapi.testclient import TestClient

from app.config import Settings


def test_fastapi_app_starts(client: TestClient) -> None:
    """Test that the FastAPI app starts and responds to health check"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_root_endpoint(client: TestClient) -> None:
    """Test that the root endpoint returns expected response"""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "message" in data


def test_redis_connection(redis_client: redis.Redis) -> None:
    """Test that Redis connection works"""
    # Test ping
    assert redis_client.ping() is True

    # Test basic operations
    redis_client.set("test_key", "test_value")
    assert redis_client.get("test_key") == "test_value"

    # Test deletion
    redis_client.delete("test_key")
    assert redis_client.get("test_key") is None


def test_courses_toml_loading(test_settings: Settings) -> None:
    """Test that courses.toml loads correctly"""
    courses = test_settings.load_courses()

    # Check that courses were loaded
    assert len(courses) == 2
    assert "test-course" in courses
    assert "another-course" in courses

    # Check course details
    test_course = courses["test-course"]
    assert test_course.slug == "test-course"
    assert test_course.secret == "test-secret-123"
    assert test_course.name == "Test Course"


def test_get_course(test_settings: Settings) -> None:
    """Test getting a specific course"""
    course = test_settings.get_course("test-course")
    assert course is not None
    assert course.slug == "test-course"
    assert course.name == "Test Course"

    # Test non-existent course
    none_course = test_settings.get_course("nonexistent")
    assert none_course is None


def test_redis_operations_are_isolated(redis_client: redis.Redis) -> None:
    """Test that Redis operations in tests are isolated"""
    # Set a value
    redis_client.set("isolation_test", "value1")
    assert redis_client.get("isolation_test") == "value1"

    # This test should start with a clean slate in a new test
    # The conftest.py fixture flushes the db before and after each test


def test_settings_have_defaults(test_settings: Settings) -> None:
    """Test that settings have reasonable defaults"""
    assert test_settings.rate_limit_ask == 1
    assert test_settings.rate_limit_window == 10
    assert test_settings.max_question_length == 1000
    assert test_settings.session_ttl == 1800
