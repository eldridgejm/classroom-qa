"""
Tests for writing session archives to disk

This test file covers:
- Atomic file writes (temp file in the same directory + rename)
- File permissions (group-readable)
- Stopping a session writes the same JSON as the Redis archive
- Disabled when archive_dir is unset
- A failed disk write still completes the stop
"""

import json
import logging
import os
import stat
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient

import app.archive_files
from app.archive_files import archive_file_path, write_archive_file
from app.auth import create_admin_cookie
from app.config import Settings
from app.models import QuestionType
from app.redis_client import RedisClient


def admin_cookies(settings: Settings) -> dict[str, str]:
    course = settings.get_course("test-course")
    assert course is not None
    return {"admin_session": create_admin_cookie("test-course", course.secret, settings.secret_key)}


class TestWriteArchiveFile:
    """write_archive_file itself"""

    def test_writes_content_to_course_subdirectory(self, tmp_path: Path) -> None:
        path = write_archive_file(tmp_path, "test-course", "arch-1-abcd", '{"a": 1}')

        assert path == tmp_path / "test-course" / "arch-1-abcd.json"
        assert path.read_text() == '{"a": 1}'

    def test_file_is_group_readable_not_world_readable(self, tmp_path: Path) -> None:
        path = write_archive_file(tmp_path, "test-course", "arch-1-abcd", "{}")

        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert stat.S_IMODE(path.parent.stat().st_mode) & 0o777 == 0o750

    def test_leaves_no_temp_files(self, tmp_path: Path) -> None:
        write_archive_file(tmp_path, "test-course", "arch-1-abcd", "{}")
        write_archive_file(tmp_path, "test-course", "arch-2-abcd", "{}")

        assert sorted(p.name for p in (tmp_path / "test-course").iterdir()) == [
            "arch-1-abcd.json",
            "arch-2-abcd.json",
        ]

    def test_writes_via_temp_file_and_rename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The final path only ever appears complete, via rename from the same directory"""
        content = json.dumps({"questions": ["x" * 10000]})
        final = tmp_path / "test-course" / "arch-1-abcd.json"
        renames = []
        real_replace = os.replace

        def spy_replace(src: str, dst: str) -> None:
            src_path, dst_path = Path(src), Path(dst)
            # Nothing at the final path until the rename
            assert not dst_path.exists()
            # The temp file is complete, in the same directory, and not *.json
            assert src_path.parent == dst_path.parent
            assert src_path.name.startswith(".")
            assert not src_path.name.endswith(".json")
            assert src_path.read_text() == content
            renames.append((src_path, dst_path))
            real_replace(src, dst)

        monkeypatch.setattr(app.archive_files.os, "replace", spy_replace)

        write_archive_file(tmp_path, "test-course", "arch-1-abcd", content)

        assert renames == [(renames[0][0], final)]
        assert final.read_text() == content

    def test_failed_write_cleans_up_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_replace(src: str, dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(app.archive_files.os, "replace", failing_replace)

        with pytest.raises(OSError, match="disk full"):
            write_archive_file(tmp_path, "test-course", "arch-1-abcd", "{}")

        assert list((tmp_path / "test-course").iterdir()) == []

    @pytest.mark.parametrize("course", ["", ".", "..", "a/b", "a\\b"])
    def test_rejects_bad_course(self, tmp_path: Path, course: str) -> None:
        with pytest.raises(ValueError):
            archive_file_path(tmp_path, course, "arch-1-abcd")

    def test_rejects_bad_session_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            archive_file_path(tmp_path, "test-course", "../evil")


class TestArchiveSessionToDisk:
    """RedisClient.archive_session / stop_session with archive_dir"""

    def test_stop_session_writes_same_json_as_redis(
        self, redis_client: redis.Redis, tmp_path: Path
    ) -> None:
        client = RedisClient(redis_client)
        client.start_session("test-course")
        qid = client.create_question("test-course", QuestionType.MCQ, ["A", "B"])
        client.submit_answer("test-course", qid, "112345678", "A")

        session_id = client.stop_session("test-course", ttl=60, archive_dir=tmp_path)

        path = tmp_path / "test-course" / f"{session_id}.json"
        assert path.read_text() == redis_client.get(client.archive_key("test-course", session_id))

        data = json.loads(path.read_text())
        assert data["session_id"] == session_id
        assert data["questions"][0]["responses"]["112345678"]["response"] == "A"

    def test_redis_ttl_unchanged(self, redis_client: redis.Redis, tmp_path: Path) -> None:
        client = RedisClient(redis_client)
        client.start_session("test-course")

        session_id = client.stop_session("test-course", ttl=60, archive_dir=tmp_path)

        assert 0 < redis_client.ttl(client.archive_key("test-course", session_id)) <= 60

    def test_no_archive_dir_writes_nothing(
        self, redis_client: redis.Redis, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        client = RedisClient(redis_client)
        client.start_session("test-course")

        client.stop_session("test-course", ttl=60)

        assert list(tmp_path.iterdir()) == []

    def test_disk_failure_still_archives_to_redis(
        self,
        redis_client: redis.Redis,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A regular file where the archive directory should be
        bad_dir = tmp_path / "not-a-dir"
        bad_dir.write_text("")

        client = RedisClient(redis_client)
        client.start_session("test-course")

        with caplog.at_level(logging.ERROR):
            session_id = client.stop_session("test-course", ttl=60, archive_dir=bad_dir)

        assert client.get_archived_session("test-course", session_id) is not None
        assert not client.is_session_live("test-course")
        assert any(
            r.levelno == logging.ERROR and session_id in r.getMessage() for r in caplog.records
        )


class TestStopRouteWritesArchive:
    """The admin stop button writes the archive to disk when configured"""

    def test_stop_route_writes_file(
        self, client: TestClient, test_settings: Settings, redis_client, tmp_path: Path
    ) -> None:
        archive_dir = tmp_path / "archives"
        test_settings.archive_dir = str(archive_dir)
        cookies = admin_cookies(test_settings)

        client.post("/test-course/admin/session/start", cookies=cookies)
        response = client.post("/test-course/admin/session/stop", cookies=cookies)
        assert response.status_code == 200

        wrapper = RedisClient(redis_client)
        [archive] = wrapper.get_archived_sessions("test-course")
        path = archive_dir / "test-course" / f"{archive['session_id']}.json"
        assert json.loads(path.read_text()) == wrapper.get_archived_session(
            "test-course", archive["session_id"]
        )

    def test_stop_route_without_archive_dir_writes_nothing(
        self,
        client: TestClient,
        test_settings: Settings,
        redis_client,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert test_settings.archive_dir is None
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        cookies = admin_cookies(test_settings)

        client.post("/test-course/admin/session/start", cookies=cookies)
        response = client.post("/test-course/admin/session/stop", cookies=cookies)

        assert response.status_code == 200
        assert len(RedisClient(redis_client).get_archived_sessions("test-course")) == 1
        assert list(work.iterdir()) == []

    def test_stop_route_succeeds_when_disk_write_fails(
        self,
        client: TestClient,
        test_settings: Settings,
        redis_client,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        test_settings.archive_dir = str(tmp_path)

        def broken_write(*args: object, **kwargs: object) -> Path:
            raise PermissionError("read-only file system")

        monkeypatch.setattr("app.redis_client.write_archive_file", broken_write)
        cookies = admin_cookies(test_settings)

        client.post("/test-course/admin/session/start", cookies=cookies)
        response = client.post("/test-course/admin/session/stop", cookies=cookies)

        assert response.status_code == 200
        assert response.json() == {"status": "stopped"}
        wrapper = RedisClient(redis_client)
        assert not wrapper.is_session_live("test-course")
        assert len(wrapper.get_archived_sessions("test-course")) == 1

    def test_stopping_when_not_live_does_not_archive(
        self, client: TestClient, test_settings: Settings, redis_client, tmp_path: Path
    ) -> None:
        test_settings.archive_dir = str(tmp_path)
        cookies = admin_cookies(test_settings)

        client.post("/test-course/admin/session/start", cookies=cookies)
        client.post("/test-course/admin/session/stop", cookies=cookies)
        response = client.post("/test-course/admin/session/stop", cookies=cookies)

        assert response.status_code == 200
        assert len(RedisClient(redis_client).get_archived_sessions("test-course")) == 1
        assert len(list((tmp_path / "test-course").iterdir())) == 1


class TestArchiveDirSetting:
    def test_default_is_unset(self) -> None:
        assert Settings().archive_dir is None

    def test_read_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ARCHIVE_DIR", "/var/lib/classroom-qa/archives")
        assert Settings().archive_dir == "/var/lib/classroom-qa/archives"
