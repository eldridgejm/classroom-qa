"""
Tests for recovering live state after a page reload or a dropped SSE connection

This test file covers:
- Starting a question ends the previous one
- Admin and student state endpoints
- Admin dashboard restores the active question on reload
- Stopping a session uses the configured archive TTL
- SSE heartbeat
"""

import asyncio

from fastapi.testclient import TestClient

import app.config
from app.auth import create_admin_cookie, create_tsn_cookie
from app.config import Settings
from app.models import QuestionType
from app.redis_client import RedisClient
from app.routes import sse


def admin_cookies(test_settings: Settings) -> dict[str, str]:
    course = test_settings.get_course("test-course")
    assert course is not None
    cookie = create_admin_cookie("test-course", course.secret, test_settings.secret_key)
    return {"admin_session": cookie}


def student_cookies(test_settings: Settings, tsn: str = "112345678") -> dict[str, str]:
    return {"student_session": create_tsn_cookie(tsn, test_settings.secret_key)}


class TestQuestionReplacement:
    """Starting a question while another is active"""

    def test_create_question_ends_previous(self, redis_client) -> None:
        client = RedisClient(redis_client)

        qid1 = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])
        qid2 = client.create_question("test-course", QuestionType.TF)

        meta1 = client.get_question_meta("test-course", qid1)
        meta2 = client.get_question_meta("test-course", qid2)
        assert meta1 is not None and meta1["ended_at"] is not None
        assert meta2 is not None and meta2["ended_at"] is None

    def test_stop_question_keeps_original_end_time(self, redis_client) -> None:
        client = RedisClient(redis_client)

        qid = client.create_question("test-course", QuestionType.TF)
        client.stop_question("test-course", qid)
        ended_at = client.get_question_meta("test-course", qid)["ended_at"]

        client.stop_question("test-course", qid)
        assert client.get_question_meta("test-course", qid)["ended_at"] == ended_at

    def test_get_active_question(self, redis_client) -> None:
        client = RedisClient(redis_client)
        assert client.get_active_question("test-course") is None

        qid = client.create_question("test-course", QuestionType.NUMERIC)
        active = client.get_active_question("test-course")
        assert active is not None and active["id"] == qid

        client.stop_question("test-course", qid)
        assert client.get_active_question("test-course") is None


class TestAdminState:
    """Admin state endpoint and dashboard restore"""

    def test_state_requires_auth(self, client: TestClient) -> None:
        response = client.get("/test-course/admin/state")
        assert response.status_code in [401, 403]

    def test_state_without_session(self, client: TestClient, test_settings: Settings) -> None:
        response = client.get("/test-course/admin/state", cookies=admin_cookies(test_settings))
        assert response.status_code == 200
        assert response.json() == {"session_live": False, "current_question": None}

    def test_state_with_active_question(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        cookies = admin_cookies(test_settings)
        client.post("/test-course/admin/session/start", cookies=cookies)
        qid = client.post(
            "/test-course/admin/question",
            json={"type": "mcq", "options": ["A", "B", "C"]},
            cookies=cookies,
        ).json()["question_id"]

        state = client.get("/test-course/admin/state", cookies=cookies).json()
        assert state == {
            "session_live": True,
            "current_question": {"id": qid, "type": "mcq", "options": ["A", "B", "C"]},
        }

        client.post(f"/test-course/admin/question/{qid}/stop", cookies=cookies)
        state = client.get("/test-course/admin/state", cookies=cookies).json()
        assert state == {"session_live": True, "current_question": None}

    def test_dashboard_restores_active_question(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        cookies = admin_cookies(test_settings)
        client.post("/test-course/admin/session/start", cookies=cookies)
        qid = client.post(
            "/test-course/admin/question", json={"type": "tf"}, cookies=cookies
        ).json()["question_id"]

        response = client.get("/test-course/admin", cookies=cookies)
        assert response.status_code == 200
        assert f'"id": "{qid}"' in response.text

    def test_stop_session_uses_configured_ttl(
        self, client: TestClient, test_settings: Settings, redis_client
    ) -> None:
        cookies = admin_cookies(test_settings)
        app.config.settings.session_ttl = 3600
        client.post("/test-course/admin/session/start", cookies=cookies)
        client.post("/test-course/admin/session/stop", cookies=cookies)

        wrapper = RedisClient(redis_client)
        session_id = wrapper.get_archived_sessions("test-course")[0]["session_id"]
        ttl = redis_client.ttl(wrapper.archive_key("test-course", session_id))
        assert 3590 < ttl <= 3600


class TestStudentState:
    """Student state endpoint"""

    def test_state_requires_tsn(self, client: TestClient) -> None:
        response = client.get("/test-course/state")
        assert response.status_code == 401

    def test_state_includes_previous_answer(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        admin = admin_cookies(test_settings)
        student = student_cookies(test_settings)

        state = client.get("/test-course/state", cookies=student).json()
        assert state == {"session_live": False, "current_question": None, "your_answer": None}

        client.post("/test-course/admin/session/start", cookies=admin)
        qid = client.post(
            "/test-course/admin/question", json={"type": "tf"}, cookies=admin
        ).json()["question_id"]

        state = client.get("/test-course/state", cookies=student).json()
        assert state == {
            "session_live": True,
            "current_question": {"id": qid, "type": "tf", "options": None},
            "your_answer": None,
        }

        client.post(
            "/test-course/answer", json={"question_id": qid, "response": True}, cookies=student
        )
        state = client.get("/test-course/state", cookies=student).json()
        assert state["your_answer"] is True


class TestHeartbeat:
    """SSE stream sends heartbeats while the channel is quiet"""

    async def test_heartbeat_sent_when_idle(
        self, test_settings: Settings, redis_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(app.config, "settings", test_settings)
        monkeypatch.setattr(sse, "HEARTBEAT_INTERVAL", 0.5)

        stream = sse.event_generator("test-course")
        try:
            assert await stream.__anext__() == ": connected\n\n"
            event = await asyncio.wait_for(stream.__anext__(), timeout=5)
            assert event.startswith("event: ping\n")
        finally:
            await stream.aclose()
