"""
Pytest configuration and fixtures
"""

from collections.abc import Generator
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient

from app.config import Settings


@pytest.fixture(scope="session")
def redis_server() -> Generator[str, None, None]:
    """
    Fixture that provides a Redis server URL for testing.
    Uses the existing Redis instance from the Nix shell.
    """
    redis_url = "redis://localhost:6379/1"  # Use database 1 for tests
    yield redis_url


@pytest.fixture(scope="function")
def redis_client(redis_server: str) -> Generator[redis.Redis, None, None]:
    """
    Fixture that provides a Redis client connected to the test database.
    Flushes the database before and after each test.
    """
    client = redis.from_url(redis_server, decode_responses=True)

    # Flush test database before test
    client.flushdb()

    yield client

    # Flush test database after test
    client.flushdb()
    client.close()


@pytest.fixture(scope="function")
def test_settings(tmp_path: Path, redis_server: str) -> Settings:
    """
    Fixture that provides test settings with a temporary courses file.
    """
    # Create a temporary courses.toml file
    courses_file = tmp_path / "courses.toml"
    courses_file.write_text("""
[courses.test-course]
secret = "test-secret-123"
name = "Test Course"

[courses.another-course]
secret = "another-secret-456"
name = "Another Test Course"
""")

    # Create test settings
    test_settings = Settings(
        redis_url=redis_server,
        secret_key="test-secret-key-for-hmac",
        courses_file=str(courses_file),
    )

    return test_settings


@pytest.fixture(scope="function")
def client(test_settings: Settings) -> Generator[TestClient, None, None]:
    """
    Fixture that provides a FastAPI test client with test settings.
    """
    # Override the global settings with test settings
    import app.config
    from app.main import app as fastapi_app

    original_settings = app.config.settings
    app.config.settings = test_settings

    # Create test client
    test_client = TestClient(fastapi_app)

    yield test_client

    # Restore original settings
    app.config.settings = original_settings


@pytest.fixture(scope="function")
def sample_courses() -> dict[str, dict[str, str]]:
    """
    Fixture that provides sample course data for testing.
    """
    return {
        "test-course": {
            "secret": "test-secret-123",
            "name": "Test Course",
        },
        "another-course": {
            "secret": "another-secret-456",
            "name": "Another Test Course",
        },
    }


@pytest.fixture(scope="function")
def mock_llm_response() -> dict[str, str]:
    """
    Fixture that provides mock LLM responses for testing.
    """
    return {
        "summary": "Students are asking about the homework deadline.",
        "grouped_questions": "When is homework due? What's the due date?",
    }
