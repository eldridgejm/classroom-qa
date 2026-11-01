"""
Tests for auto-stopping idle sessions

This test file covers:
- Activity tracking (session start, question start/stop, answers, student questions)
- Idle detection and the configurable timeout
- Auto-stop archives (Redis + disk) and publishes SESSION_STOPPED
- A session is stopped only once, even with concurrent checkers
- The background task started in the app's lifespan
"""

import json
import threading
import time
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient

import app.config
from app.auth import create_admin_cookie
from app.config import Settings
from app.main import app as fastapi_app
from app.main import check_idle_sessions_once
from app.models import QuestionType
from app.redis_client import RedisClient
from app.services.sessions import stop_idle_sessions, stop_session_if_idle

COURSE = "test-course"
TIMEOUT = 7200


@pytest.fixture
def settings(test_settings: Settings, tmp_path: Path) -> Settings:
    test_settings.archive_dir = str(tmp_path / "archives")
    return test_settings


@pytest.fixture
def wrapper(redis_client: redis.Redis) -> RedisClient:
    return RedisClient(redis_client)


def make_idle(wrapper: RedisClient, course: str = COURSE, seconds: float = TIMEOUT + 1) -> None:
    """Pretend the last activity happened `seconds` ago"""
    wrapper.redis.set(wrapper.last_activity_key(course), repr(time.time() - seconds))


class TestActivityTracking:
    def test_settings_defaults(self) -> None:
        settings = Settings()
        assert settings.idle_session_timeout == 7200
        assert settings.idle_check_interval == 300

    def test_start_session_records_activity(self, wrapper: RedisClient) -> None:
        before = time.time()
        wrapper.start_session(COURSE)

        last = wrapper.get_last_activity(COURSE)
        assert last is not None and before <= last <= time.time()

    def test_no_activity_recorded(self, wrapper: RedisClient) -> None:
        assert wrapper.get_last_activity(COURSE) is None

    def test_stop_session_clears_activity(self, wrapper: RedisClient) -> None:
        wrapper.start_session(COURSE)
        wrapper.stop_session(COURSE, ttl=60)

        assert wrapper.get_last_activity(COURSE) is None

    @pytest.mark.parametrize(
        "activity",
        ["create_question", "stop_question", "submit_answer", "submit_question"],
    )
    def test_activity_resets_clock(
        self, wrapper: RedisClient, settings: Settings, activity: str
    ) -> None:
        wrapper.start_session(COURSE)
        qid = wrapper.create_question(COURSE, QuestionType.TF)
        make_idle(wrapper)

        if activity == "create_question":
            wrapper.create_question(COURSE, QuestionType.MCQ, ["A", "B"])
        elif activity == "stop_question":
            wrapper.stop_question(COURSE, qid)
        elif activity == "submit_answer":
            wrapper.submit_answer(COURSE, qid, "112345678", True)
        else:
            wrapper.submit_question(COURSE, "112345678", "Why?")

        last = wrapper.get_last_activity(COURSE)
        assert last is not None and time.time() - last < 5
        assert stop_session_if_idle(wrapper, COURSE, settings) is None
        assert wrapper.is_session_live(COURSE)

    def test_routes_record_activity(
        self, client: TestClient, settings: Settings, wrapper: RedisClient
    ) -> None:
        """Activity through the HTTP routes (as students and admins use it) counts"""
        from app.auth import create_tsn_cookie

        course = settings.get_course(COURSE)
        assert course is not None
        admin = {"admin_session": create_admin_cookie(COURSE, course.secret, settings.secret_key)}
        student = {"student_session": create_tsn_cookie("112345678", settings.secret_key)}

        client.post(f"/{COURSE}/admin/session/start", cookies=admin)
        qid = client.post(f"/{COURSE}/admin/question", json={"type": "tf"}, cookies=admin).json()[
            "question_id"
        ]

        make_idle(wrapper)
        response = client.post(
            f"/{COURSE}/answer", json={"question_id": qid, "response": True}, cookies=student
        )
        assert response.status_code == 200
        assert time.time() - (wrapper.get_last_activity(COURSE) or 0) < 5

        make_idle(wrapper)
        client.post(f"/{COURSE}/admin/question/{qid}/stop", cookies=admin)
        assert time.time() - (wrapper.get_last_activity(COURSE) or 0) < 5


class TestIdleDetection:
    def test_not_stopped_before_timeout(self, wrapper: RedisClient, settings: Settings) -> None:
        wrapper.start_session(COURSE)
        last = wrapper.get_last_activity(COURSE)
        assert last is not None

        assert stop_session_if_idle(wrapper, COURSE, settings, now=last + TIMEOUT - 1) is None
        assert wrapper.is_session_live(COURSE)

    def test_stopped_after_timeout(self, wrapper: RedisClient, settings: Settings) -> None:
        wrapper.start_session(COURSE)
        qid = wrapper.create_question(COURSE, QuestionType.MCQ, ["A", "B"])
        wrapper.submit_answer(COURSE, qid, "112345678", "B")
        last = wrapper.get_last_activity(COURSE)
        assert last is not None

        session_id = stop_session_if_idle(wrapper, COURSE, settings, now=last + TIMEOUT)

        assert session_id is not None
        assert not wrapper.is_session_live(COURSE)
        archive = wrapper.get_archived_session(COURSE, session_id)
        assert archive is not None
        assert archive["questions"][0]["responses"]["112345678"]["response"] == "B"
        # Same as the stop button: archive TTL and file on disk
        assert redis_ttl(wrapper, session_id) > 0
        path = Path(settings.archive_dir or "") / COURSE / f"{session_id}.json"
        assert json.loads(path.read_text()) == archive

    def test_timeout_is_configurable(self, wrapper: RedisClient, settings: Settings) -> None:
        settings.idle_session_timeout = 60
        wrapper.start_session(COURSE)
        last = wrapper.get_last_activity(COURSE)
        assert last is not None

        assert stop_session_if_idle(wrapper, COURSE, settings, now=last + 59) is None
        assert stop_session_if_idle(wrapper, COURSE, settings, now=last + 60) is not None

    def test_zero_timeout_disables(self, wrapper: RedisClient, settings: Settings) -> None:
        settings.idle_session_timeout = 0
        wrapper.start_session(COURSE)
        make_idle(wrapper, seconds=10**6)

        assert stop_session_if_idle(wrapper, COURSE, settings) is None
        assert wrapper.is_session_live(COURSE)

    def test_no_session(self, wrapper: RedisClient, settings: Settings) -> None:
        make_idle(wrapper)
        assert stop_session_if_idle(wrapper, COURSE, settings) is None
        assert wrapper.get_archived_sessions(COURSE) == []

    def test_missing_activity_starts_clock(self, wrapper: RedisClient, settings: Settings) -> None:
        """A session live from before activity tracking isn't stopped immediately"""
        wrapper.start_session(COURSE)
        wrapper.redis.delete(wrapper.last_activity_key(COURSE))

        assert stop_session_if_idle(wrapper, COURSE, settings) is None
        assert wrapper.is_session_live(COURSE)
        last = wrapper.get_last_activity(COURSE)
        assert last is not None and time.time() - last < 5

    def test_only_idle_courses_stopped(self, wrapper: RedisClient, settings: Settings) -> None:
        wrapper.start_session("test-course")
        wrapper.start_session("another-course")
        make_idle(wrapper, "test-course")

        stopped = stop_idle_sessions(wrapper, settings)

        assert [course for course, _ in stopped] == ["test-course"]
        assert not wrapper.is_session_live("test-course")
        assert wrapper.is_session_live("another-course")


def redis_ttl(wrapper: RedisClient, session_id: str) -> int:
    return int(wrapper.redis.ttl(wrapper.archive_key(COURSE, session_id)))


class TestStopOnce:
    def test_second_check_does_nothing(self, wrapper: RedisClient, settings: Settings) -> None:
        wrapper.start_session(COURSE)
        make_idle(wrapper)

        assert stop_session_if_idle(wrapper, COURSE, settings) is not None
        assert stop_session_if_idle(wrapper, COURSE, settings) is None
        assert len(wrapper.get_archived_sessions(COURSE)) == 1

    def test_concurrent_checkers_stop_once(
        self, redis_server: str, wrapper: RedisClient, settings: Settings
    ) -> None:
        wrapper.start_session(COURSE)
        wrapper.create_question(COURSE, QuestionType.TF)
        make_idle(wrapper)

        results: list[str | None] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            conn = redis.from_url(redis_server, decode_responses=True)
            try:
                barrier.wait()
                results.append(stop_session_if_idle(RedisClient(conn), COURSE, settings))
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len([r for r in results if r is not None]) == 1
        assert len(wrapper.get_archived_sessions(COURSE)) == 1
        assert len(list((Path(settings.archive_dir or "") / COURSE).iterdir())) == 1

    def test_skips_while_another_stop_holds_lock(
        self, wrapper: RedisClient, settings: Settings
    ) -> None:
        wrapper.start_session(COURSE)
        make_idle(wrapper)
        lock = wrapper.redis.lock(wrapper.stop_lock_key(COURSE), timeout=10)
        assert lock.acquire(blocking=False)
        try:
            assert stop_session_if_idle(wrapper, COURSE, settings) is None
            assert wrapper.is_session_live(COURSE)
        finally:
            lock.release()

    def test_stop_button_after_auto_stop_does_not_archive_again(
        self, client: TestClient, settings: Settings, wrapper: RedisClient
    ) -> None:
        course = settings.get_course(COURSE)
        assert course is not None
        admin = {"admin_session": create_admin_cookie(COURSE, course.secret, settings.secret_key)}

        client.post(f"/{COURSE}/admin/session/start", cookies=admin)
        make_idle(wrapper)
        assert stop_session_if_idle(wrapper, COURSE, settings) is not None

        response = client.post(f"/{COURSE}/admin/session/stop", cookies=admin)
        assert response.status_code == 200
        assert len(wrapper.get_archived_sessions(COURSE)) == 1

    def test_stop_button_waits_then_gives_up_on_held_lock(
        self,
        client: TestClient,
        settings: Settings,
        wrapper: RedisClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("app.services.sessions.STOP_LOCK_WAIT", 0.2)
        course = settings.get_course(COURSE)
        assert course is not None
        admin = {"admin_session": create_admin_cookie(COURSE, course.secret, settings.secret_key)}
        client.post(f"/{COURSE}/admin/session/start", cookies=admin)

        lock = wrapper.redis.lock(wrapper.stop_lock_key(COURSE), timeout=10)
        assert lock.acquire(blocking=False)
        try:
            response = client.post(f"/{COURSE}/admin/session/stop", cookies=admin)
        finally:
            lock.release()

        assert response.status_code == 409
        assert wrapper.is_session_live(COURSE)
        assert wrapper.get_archived_sessions(COURSE) == []


class TestEvents:
    def test_auto_stop_publishes_session_stopped(
        self, wrapper: RedisClient, settings: Settings, redis_client: redis.Redis
    ) -> None:
        wrapper.start_session(COURSE)
        make_idle(wrapper)

        pubsub = redis_client.pubsub()
        pubsub.subscribe(wrapper.events_channel_key(COURSE))
        try:
            assert pubsub.get_message(timeout=1)["type"] == "subscribe"  # type: ignore[index]

            stop_session_if_idle(wrapper, COURSE, settings)

            message = pubsub.get_message(timeout=2)
            assert message is not None
            assert json.loads(message["data"]) == {"event": "session_stopped", "data": {}}
            # Exactly one event
            assert pubsub.get_message(timeout=0.2) is None
        finally:
            pubsub.close()

    def test_not_idle_publishes_nothing(
        self, wrapper: RedisClient, settings: Settings, redis_client: redis.Redis
    ) -> None:
        wrapper.start_session(COURSE)

        pubsub = redis_client.pubsub()
        pubsub.subscribe(wrapper.events_channel_key(COURSE))
        try:
            pubsub.get_message(timeout=1)
            stop_session_if_idle(wrapper, COURSE, settings)
            assert pubsub.get_message(timeout=0.2) is None
        finally:
            pubsub.close()


class TestBackgroundTask:
    def test_check_idle_sessions_once(
        self, wrapper: RedisClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app.config, "settings", settings)
        wrapper.start_session(COURSE)
        make_idle(wrapper)

        check_idle_sessions_once()

        assert not wrapper.is_session_live(COURSE)
        assert len(wrapper.get_archived_sessions(COURSE)) == 1

    def test_lifespan_runs_monitor(
        self, wrapper: RedisClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings.idle_check_interval = 1
        monkeypatch.setattr(app.config, "settings", settings)
        wrapper.start_session(COURSE)
        make_idle(wrapper)

        with TestClient(fastapi_app):
            deadline = time.time() + 5
            while wrapper.is_session_live(COURSE) and time.time() < deadline:
                time.sleep(0.1)

        assert not wrapper.is_session_live(COURSE)
        assert len(wrapper.get_archived_sessions(COURSE)) == 1
