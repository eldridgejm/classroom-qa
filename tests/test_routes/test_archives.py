"""
Tests for admin archive routes

This test file covers:
- Session archiving on stop
- Session cleanup on start
- Archive listing endpoint
- Archive download endpoint
- Archive expiration (TTL)
- Edge cases
"""

import time

from fastapi.testclient import TestClient

from app.auth import create_admin_cookie
from app.config import Settings
from app.models import QuestionType


class TestSessionArchiving:
    """Test cases for session archiving on stop"""

    def test_stop_session_creates_archive(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that stopping a session creates an archive"""
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
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "A")

        # Stop session
        response = client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200

        # Verify archive was created
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1
        assert "session_id" in archives[0]
        assert "started_at" in archives[0]
        assert "stopped_at" in archives[0]
        assert "question_count" in archives[0]
        assert archives[0]["question_count"] == 1

    def test_stop_session_archive_contains_full_data(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that archived session contains all question/response data"""
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
        redis_client_wrapper.submit_answer("test-course", qid1, "A12345679", "B")

        qid2 = redis_client_wrapper.create_question("test-course", QuestionType.TF)
        redis_client_wrapper.submit_answer("test-course", qid2, "A12345678", True)

        # Stop session
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Get archived session
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        session_id = archives[0]["session_id"]

        archive_data = redis_client_wrapper.get_archived_session(
            "test-course", session_id
        )

        assert archive_data is not None
        assert "questions" in archive_data
        assert len(archive_data["questions"]) == 2

        # Verify question data
        question_ids = [q["question_id"] for q in archive_data["questions"]]
        assert qid1 in question_ids
        assert qid2 in question_ids

        # Find MCQ question and verify responses
        mcq = next(q for q in archive_data["questions"] if q["type"] == "mcq")
        assert len(mcq["responses"]) == 2
        assert "A12345678" in mcq["responses"]
        assert "A12345679" in mcq["responses"]

    def test_stop_session_clears_current_data(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that stopping session clears current session data"""
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

        # Verify data exists before stop
        assert redis_client_wrapper.get_question_meta("test-course", qid) is not None
        assert len(redis_client_wrapper.get_all_question_ids("test-course")) == 1

        # Stop session
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Verify current data is cleared
        assert not redis_client_wrapper.is_session_live("test-course")
        assert redis_client_wrapper.get_current_question("test-course") is None
        assert len(redis_client_wrapper.get_all_question_ids("test-course")) == 0

    def test_stop_empty_session_creates_empty_archive(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that stopping session with no questions creates empty archive"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Start and stop session without creating questions
        redis_client_wrapper = RedisClient(redis_client)
        redis_client_wrapper.start_session("test-course")

        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Verify empty archive was created
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1
        assert archives[0]["question_count"] == 0


class TestSessionCleanup:
    """Test cases for session cleanup on start"""

    def test_start_session_clears_old_data(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that starting a new session clears old session data"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # First session
        redis_client_wrapper.start_session("test-course")
        qid1 = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid1, "A12345678", "A")

        # Stop session (archives data)
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Verify archive exists
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1

        # Start new session
        response = client.post(
            "/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200

        # Verify current session data is clean
        assert redis_client_wrapper.is_session_live("test-course")
        assert redis_client_wrapper.get_current_question("test-course") is None
        assert len(redis_client_wrapper.get_all_question_ids("test-course")) == 0

        # Verify archive still exists
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1

    def test_multiple_session_cycles(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test multiple session start/stop cycles create separate archives"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # Session 1
        redis_client_wrapper.start_session("test-course")
        qid1 = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )
        redis_client_wrapper.submit_answer("test-course", qid1, "A12345678", "A")
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        time.sleep(0.1)  # Ensure different timestamps

        # Session 2
        client.post(
            "/test-course/admin/session/start",
            cookies={"admin_session": admin_cookie},
        )
        qid2 = redis_client_wrapper.create_question(
            "test-course", QuestionType.TF
        )
        redis_client_wrapper.submit_answer("test-course", qid2, "A12345679", True)
        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Verify two separate archives exist
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 2

        # Verify each archive has correct data
        archive1 = redis_client_wrapper.get_archived_session(
            "test-course", archives[0]["session_id"]
        )
        archive2 = redis_client_wrapper.get_archived_session(
            "test-course", archives[1]["session_id"]
        )

        # One should have MCQ, other should have T/F
        types = [
            archive1["questions"][0]["type"],
            archive2["questions"][0]["type"],
        ]
        assert "mcq" in types
        assert "tf" in types


class TestArchiveRoutes:
    """Test cases for archive listing and download routes"""

    def test_archives_page_lists_sessions(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that archives page lists archived sessions"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # Create two archived sessions
        for i in range(2):
            redis_client_wrapper.start_session("test-course")
            qid = redis_client_wrapper.create_question(
                "test-course", QuestionType.MCQ, ["A", "B"]
            )
            redis_client_wrapper.submit_answer("test-course", qid, f"A1234567{i}", "A")
            client.post(
                "/test-course/admin/session/stop",
                cookies={"admin_session": admin_cookie},
            )
            time.sleep(0.1)

        # Get archives page
        response = client.get(
            "/test-course/admin/archives",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

        # Check that both archives are listed
        html = response.text
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        for archive in archives:
            assert archive["session_id"] in html

    def test_archive_download(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test downloading an archived session"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # Create archived session
        redis_client_wrapper.start_session("test-course")
        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B", "C"]
        )
        redis_client_wrapper.submit_answer("test-course", qid, "A12345678", "B")

        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Get session_id
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        session_id = archives[0]["session_id"]

        # Download archive
        response = client.get(
            f"/test-course/admin/archives/{session_id}",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert data["session_id"] == session_id
        assert "started_at" in data
        assert "stopped_at" in data
        assert len(data["questions"]) == 1
        assert data["questions"][0]["type"] == "mcq"

    def test_archives_page_no_archives(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test archives page when no archives exist"""
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/test-course/admin/archives",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 200
        assert "No archived sessions" in response.text

    def test_archive_download_not_found(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test downloading non-existent archive returns 404"""
        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/test-course/admin/archives/nonexistent-id",
            cookies={"admin_session": admin_cookie},
        )

        assert response.status_code == 404

    def test_archives_require_auth(self, client: TestClient) -> None:
        """Test that archives endpoints require authentication"""
        # Archives page
        response = client.get("/test-course/admin/archives")
        assert response.status_code == 403

        # Archive download
        response = client.get("/test-course/admin/archives/some-id")
        assert response.status_code == 403


class TestArchiveTTL:
    """Test cases for archive expiration"""

    def test_archive_has_ttl(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that archived sessions have TTL set"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # Create archived session
        redis_client_wrapper.start_session("test-course")
        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )

        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Get archive key and check TTL
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        session_id = archives[0]["session_id"]

        archive_key = f"course:test-course:archive:{session_id}"
        ttl = redis_client.ttl(archive_key)

        # Should have TTL set (30 minutes = 1800 seconds)
        assert 1790 < ttl <= 1800

    def test_old_archives_expire(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        """Test that old archives expire and are not listed"""
        from app.redis_client import RedisClient

        course = test_settings.get_course("test-course")
        assert course is not None

        admin_cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        redis_client_wrapper = RedisClient(redis_client)

        # Create archived session
        redis_client_wrapper.start_session("test-course")
        qid = redis_client_wrapper.create_question(
            "test-course", QuestionType.MCQ, ["A", "B"]
        )

        client.post(
            "/test-course/admin/session/stop",
            cookies={"admin_session": admin_cookie},
        )

        # Verify archive exists
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 1
        session_id = archives[0]["session_id"]

        # Manually expire the archive (set TTL to 1 second)
        archive_key = f"course:test-course:archive:{session_id}"
        redis_client.expire(archive_key, 1)

        # Wait for expiration
        time.sleep(1.5)

        # Verify archive no longer exists
        archives = redis_client_wrapper.get_archived_sessions("test-course")
        assert len(archives) == 0
