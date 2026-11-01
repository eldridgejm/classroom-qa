"""
Tests for uploading attendance sheets

This test file covers:
- Identifying files by content (JPEG, PNG, HEIC, PDF), not name or claimed type
- Storage layout: <sheets_dir>/<course>/<timestamp>-<id>/page-NN.jpg + manifest.json
- File and directory modes, atomic writes, the manifest written last
- Cleanup when a write fails; listing skips incomplete uploads
- Routes: admin auth required, validation, limits, disabled when sheets_dir is unset
"""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis
from fastapi.testclient import TestClient

import app.archive_files
import app.config
from app.auth import create_admin_cookie
from app.config import Settings
from app.sheet_files import (
    HEIC,
    JPEG,
    PDF,
    PNG,
    SheetFile,
    list_sheet_uploads,
    sniff_sheet_type,
    write_sheet_upload,
)

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 200 + b"\xff\xd9"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF_BYTES = b"%PDF-1.7\n" + b"x" * 100 + b"\n%%EOF\n"
HEIC_BYTES = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 50


def admin_cookies(settings: Settings, course: str = "test-course") -> dict[str, str]:
    config = settings.get_course(course)
    assert config is not None
    return {"admin_session": create_admin_cookie(course, config.secret, settings.secret_key)}


@pytest.fixture
def sheets_dir(tmp_path: Path, test_settings: Settings) -> Path:
    d = tmp_path / "sheets"
    test_settings.sheets_dir = str(d)
    return d


@pytest.fixture
def admin_client(
    client: TestClient, test_settings: Settings, redis_client: redis.Redis
) -> TestClient:
    client.cookies.update(admin_cookies(test_settings))
    return client


def upload_dirs(sheets_dir: Path, course: str = "test-course") -> list[Path]:
    course_dir = sheets_dir / course
    if not course_dir.exists():
        return []
    return sorted(p for p in course_dir.iterdir())


class TestSniffSheetType:
    def test_known_types(self) -> None:
        assert sniff_sheet_type(JPEG_BYTES) == JPEG
        assert sniff_sheet_type(PNG_BYTES) == PNG
        assert sniff_sheet_type(PDF_BYTES) == PDF
        assert sniff_sheet_type(HEIC_BYTES) == HEIC

    def test_generic_heif_brand_needs_heic_compatible_brand(self) -> None:
        heif = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic"
        avif = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1avif"
        assert sniff_sheet_type(heif) == HEIC
        assert sniff_sheet_type(avif) is None

    @pytest.mark.parametrize(
        "data",
        [b"", b"hello world", b"GIF89a....", b"<html>", b"\x00\x00\x00\x18ftypavif" + b"\x00" * 8],
    )
    def test_rejects_other_content(self, data: bytes) -> None:
        assert sniff_sheet_type(data) is None


class TestWriteSheetUpload:
    def test_layout_and_manifest(self, tmp_path: Path) -> None:
        now = datetime(2026, 11, 3, 18, 30, 12, tzinfo=UTC)
        files = [
            SheetFile(JPEG_BYTES, JPEG, "IMG_1.jpg"),
            SheetFile(PNG_BYTES, PNG, "IMG_2.png"),
            SheetFile(PDF_BYTES, PDF, "scan.pdf"),
        ]

        manifest = write_sheet_upload(tmp_path, "dsc190", files, 7, now=now)

        [upload_dir] = list((tmp_path / "dsc190").iterdir())
        assert upload_dir.name.startswith("20261103T183012Z-")
        assert upload_dir.name == manifest["id"]
        assert sorted(p.name for p in upload_dir.iterdir()) == [
            "manifest.json",
            "page-01.jpg",
            "page-02.png",
            "scan-03.pdf",
        ]
        assert (upload_dir / "page-01.jpg").read_bytes() == JPEG_BYTES
        assert (upload_dir / "scan-03.pdf").read_bytes() == PDF_BYTES

        on_disk = json.loads((upload_dir / "manifest.json").read_text())
        assert on_disk == manifest
        assert on_disk["uploaded_at"] == "2026-11-03T18:30:12Z"
        assert on_disk["course"] == "dsc190"
        assert on_disk["lecture"] == 7
        assert [f["name"] for f in on_disk["files"]] == [
            "page-01.jpg",
            "page-02.png",
            "scan-03.pdf",
        ]
        assert on_disk["files"][0]["type"] == "image/jpeg"
        assert on_disk["files"][2]["type"] == "application/pdf"
        assert on_disk["files"][0]["bytes"] == len(JPEG_BYTES)
        assert len(on_disk["files"][0]["sha256"]) == 64
        assert on_disk["files"][0]["original_name"] == "IMG_1.jpg"

    def test_lecture_null(self, tmp_path: Path) -> None:
        manifest = write_sheet_upload(tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG)], None)
        assert manifest["lecture"] is None
        upload_dir = tmp_path / "dsc190" / manifest["id"]
        assert json.loads((upload_dir / "manifest.json").read_text())["lecture"] is None

    def test_modes(self, tmp_path: Path) -> None:
        manifest = write_sheet_upload(tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG)], None)
        upload_dir = tmp_path / "dsc190" / manifest["id"]

        assert stat.S_IMODE(upload_dir.stat().st_mode) == 0o750
        assert stat.S_IMODE((tmp_path / "dsc190").stat().st_mode) == 0o750
        for f in upload_dir.iterdir():
            assert stat.S_IMODE(f.stat().st_mode) == 0o640, f

    def test_separate_uploads_get_separate_directories(self, tmp_path: Path) -> None:
        now = datetime(2026, 11, 3, 18, 30, 12, tzinfo=UTC)
        a = write_sheet_upload(tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG)], 1, now=now)
        b = write_sheet_upload(tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG)], 2, now=now)
        assert a["id"] != b["id"]
        assert len(list((tmp_path / "dsc190").iterdir())) == 2

    def test_every_file_written_atomically_and_manifest_last(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each file appears only via rename of a complete temp file; manifest.json is the last"""
        renamed: list[str] = []
        real_replace = os.replace

        def spy_replace(src: str, dst: str) -> None:
            src_path, dst_path = Path(src), Path(dst)
            assert not dst_path.exists()
            assert src_path.parent == dst_path.parent
            assert src_path.name.startswith(".") and src_path.name.endswith(".tmp")
            # Nothing else is visible before the manifest is complete
            if dst_path.name == "manifest.json":
                manifest = json.loads(src_path.read_text())
                for entry in manifest["files"]:
                    assert (dst_path.parent / entry["name"]).exists()
            else:
                assert not (dst_path.parent / "manifest.json").exists()
            renamed.append(dst_path.name)
            real_replace(src, dst)

        monkeypatch.setattr(app.archive_files.os, "replace", spy_replace)

        files = [
            SheetFile(JPEG_BYTES, JPEG),
            SheetFile(JPEG_BYTES, JPEG),
            SheetFile(PDF_BYTES, PDF),
        ]
        manifest = write_sheet_upload(tmp_path, "dsc190", files, None)

        assert renamed == ["page-01.jpg", "page-02.jpg", "scan-03.pdf", "manifest.json"]
        upload_dir = tmp_path / "dsc190" / manifest["id"]
        assert not [p for p in upload_dir.iterdir() if p.name.startswith(".")]

    def test_failure_removes_partial_upload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_replace = os.replace
        calls = 0

        def failing_replace(src: str, dst: str) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            real_replace(src, dst)

        monkeypatch.setattr(app.archive_files.os, "replace", failing_replace)

        with pytest.raises(OSError, match="disk full"):
            write_sheet_upload(
                tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG), SheetFile(JPEG_BYTES, JPEG)], 3
            )

        assert list((tmp_path / "dsc190").iterdir()) == []

    @pytest.mark.parametrize("course", ["", ".", "..", "a/b", ".hidden"])
    def test_rejects_bad_course(self, tmp_path: Path, course: str) -> None:
        with pytest.raises(ValueError):
            write_sheet_upload(tmp_path, course, [SheetFile(JPEG_BYTES, JPEG)], None)

    def test_rejects_empty_upload(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            write_sheet_upload(tmp_path, "dsc190", [], None)


class TestListSheetUploads:
    def test_newest_first_and_skips_incomplete(self, tmp_path: Path) -> None:
        old = write_sheet_upload(
            tmp_path,
            "dsc190",
            [SheetFile(JPEG_BYTES, JPEG)],
            1,
            now=datetime(2026, 11, 3, 10, 0, tzinfo=UTC),
        )
        new = write_sheet_upload(
            tmp_path,
            "dsc190",
            [SheetFile(JPEG_BYTES, JPEG)],
            2,
            now=datetime(2026, 11, 5, 10, 0, tzinfo=UTC),
        )
        # An upload still being written: no manifest yet
        (tmp_path / "dsc190" / "20261106T100000Z-deadbeef").mkdir()
        # A temp entry
        (tmp_path / "dsc190" / ".partial").mkdir()

        uploads = list_sheet_uploads(tmp_path, "dsc190")

        assert [u["id"] for u in uploads] == [new["id"], old["id"]]

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert list_sheet_uploads(tmp_path, "dsc190") == []

    def test_limit(self, tmp_path: Path) -> None:
        for _ in range(5):
            write_sheet_upload(tmp_path, "dsc190", [SheetFile(JPEG_BYTES, JPEG)], None)
        assert len(list_sheet_uploads(tmp_path, "dsc190", limit=3)) == 3


class TestSheetRoutesAuth:
    """Every sheets route requires admin auth for that course"""

    def test_page_shows_login_when_logged_out(
        self, client: TestClient, sheets_dir: Path, redis_client: redis.Redis
    ) -> None:
        response = client.get("/test-course/admin/sheets")
        assert response.status_code == 200
        assert "Admin Login" in response.text
        assert 'name="next" value="sheets"' in response.text
        assert "Take photo" not in response.text

    def test_upload_requires_admin(
        self, client: TestClient, sheets_dir: Path, redis_client: redis.Redis
    ) -> None:
        response = client.post(
            "/test-course/admin/sheets", files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))]
        )
        assert response.status_code == 403
        assert upload_dirs(sheets_dir) == []

    def test_upload_requires_admin_of_that_course(
        self,
        client: TestClient,
        test_settings: Settings,
        sheets_dir: Path,
        redis_client: redis.Redis,
    ) -> None:
        client.cookies.update(admin_cookies(test_settings, "another-course"))
        response = client.post(
            "/test-course/admin/sheets", files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))]
        )
        assert response.status_code == 403
        assert upload_dirs(sheets_dir) == []

    def test_recent_requires_admin(
        self, client: TestClient, sheets_dir: Path, redis_client: redis.Redis
    ) -> None:
        assert client.get("/test-course/admin/sheets/recent").status_code == 403

    def test_login_returns_to_sheets_page(
        self, client: TestClient, sheets_dir: Path, redis_client: redis.Redis
    ) -> None:
        response = client.post(
            "/test-course/admin/login",
            data={"secret": "test-secret-123", "next": "sheets"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/test-course/admin/sheets"

    def test_login_next_is_not_an_open_redirect(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        response = client.post(
            "/test-course/admin/login",
            data={"secret": "test-secret-123", "next": "//evil.example/"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/test-course/admin"

    def test_unknown_course(self, admin_client: TestClient, sheets_dir: Path) -> None:
        assert admin_client.get("/nope/admin/sheets").status_code == 404


class TestSheetsPage:
    def test_page_renders(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.get("/test-course/admin/sheets")
        assert response.status_code == 200
        assert 'id="shutter"' in response.text
        assert 'capture="environment"' in response.text
        assert 'accept="image/*,application/pdf"' in response.text
        assert "None yet." in response.text

    def test_page_lists_recent_uploads(self, admin_client: TestClient, sheets_dir: Path) -> None:
        write_sheet_upload(
            sheets_dir,
            "test-course",
            [SheetFile(JPEG_BYTES, JPEG)] * 2,
            7,
            now=datetime(2026, 11, 3, 18, 0, tzinfo=UTC),
        )
        response = admin_client.get("/test-course/admin/sheets")
        assert "Lecture 07" in response.text
        assert "2 pages" in response.text
        assert 'datetime="2026-11-03T18:00:00Z"' in response.text

    def test_page_when_disabled(self, admin_client: TestClient) -> None:
        assert not app.config.settings.sheets_dir
        response = admin_client.get("/test-course/admin/sheets")
        assert response.status_code == 200
        assert "not enabled" in response.text
        assert 'id="shutter"' not in response.text

    def test_dashboard_links_to_sheets_when_enabled(
        self, admin_client: TestClient, sheets_dir: Path
    ) -> None:
        response = admin_client.get("/test-course/admin")
        assert "/test-course/admin/sheets" in response.text

    def test_dashboard_has_no_sheets_link_when_disabled(self, admin_client: TestClient) -> None:
        response = admin_client.get("/test-course/admin")
        assert "/test-course/admin/sheets" not in response.text

    def test_root_path(
        self, client: TestClient, test_settings: Settings, sheets_dir: Path, redis_client
    ) -> None:
        qa_client = TestClient(client.app, root_path="/qa")
        qa_client.cookies.update(admin_cookies(test_settings))
        response = qa_client.get("/qa/test-course/admin/sheets")
        assert response.status_code == 200
        assert '"/qa/test-course/admin/sheets"' in response.text  # upload URL for the script
        assert 'href="/qa/test-course/admin"' in response.text


class TestUploadRoute:
    def test_upload_photos(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets",
            data={"lecture": "07"},
            files=[
                ("files", ("page-01.jpg", JPEG_BYTES, "image/jpeg")),
                ("files", ("page-02.jpg", JPEG_BYTES + b"2", "image/jpeg")),
            ],
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["lecture"] == 7
        assert body["images"] == 2
        assert body["pdfs"] == 0

        [upload_dir] = upload_dirs(sheets_dir)
        assert upload_dir.name == body["id"]
        assert sorted(p.name for p in upload_dir.iterdir()) == [
            "manifest.json",
            "page-01.jpg",
            "page-02.jpg",
        ]
        assert (upload_dir / "page-02.jpg").read_bytes() == JPEG_BYTES + b"2"
        manifest = json.loads((upload_dir / "manifest.json").read_text())
        assert manifest["course"] == "test-course"
        assert manifest["lecture"] == 7

        recent = admin_client.get("/test-course/admin/sheets/recent").json()
        assert recent == [
            {
                "id": body["id"],
                "uploaded_at": body["uploaded_at"],
                "lecture": 7,
                "images": 2,
                "pdfs": 0,
            }
        ]

    @pytest.mark.parametrize("lecture", [None, "", "  "])
    def test_blank_lecture_means_next(
        self, admin_client: TestClient, sheets_dir: Path, lecture: str | None
    ) -> None:
        data = {} if lecture is None else {"lecture": lecture}
        response = admin_client.post(
            "/test-course/admin/sheets",
            data=data,
            files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))],
        )
        assert response.status_code == 200, response.text
        assert response.json()["lecture"] is None
        [upload_dir] = upload_dirs(sheets_dir)
        assert json.loads((upload_dir / "manifest.json").read_text())["lecture"] is None

    @pytest.mark.parametrize("lecture", ["abc", "100", "-1", "1.5", "٣"])
    def test_bad_lecture(self, admin_client: TestClient, sheets_dir: Path, lecture: str) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets",
            data={"lecture": lecture},
            files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))],
        )
        assert response.status_code == 422
        assert upload_dirs(sheets_dir) == []

    def test_type_is_checked_by_content_not_name(
        self, admin_client: TestClient, sheets_dir: Path
    ) -> None:
        # A real PDF with a .jpg name and image type is stored as a PDF
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", ("photo.jpg", PDF_BYTES, "image/jpeg"))],
        )
        assert response.status_code == 200, response.text
        [upload_dir] = upload_dirs(sheets_dir)
        assert sorted(p.name for p in upload_dir.iterdir()) == ["manifest.json", "scan-01.pdf"]

    def test_rejects_unsupported_content(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[
                ("files", ("ok.jpg", JPEG_BYTES, "image/jpeg")),
                ("files", ("evil.jpg", b"<script>alert(1)</script>", "image/jpeg")),
            ],
        )
        assert response.status_code == 415
        assert "File 2" in response.json()["detail"]
        assert upload_dirs(sheets_dir) == []

    def test_accepts_png_heic(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[
                ("files", ("a.png", PNG_BYTES, "image/png")),
                ("files", ("b.heic", HEIC_BYTES, "image/heic")),
            ],
        )
        assert response.status_code == 200, response.text
        [upload_dir] = upload_dirs(sheets_dir)
        assert sorted(p.name for p in upload_dir.iterdir()) == [
            "manifest.json",
            "page-01.png",
            "page-02.heic",
        ]

    def test_no_files(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.post("/test-course/admin/sheets", data={"lecture": "3"})
        assert response.status_code == 422
        assert upload_dirs(sheets_dir) == []

    def test_empty_file(self, admin_client: TestClient, sheets_dir: Path) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets", files=[("files", ("p.jpg", b"", "image/jpeg"))]
        )
        assert response.status_code == 422

    def test_file_too_large(
        self, admin_client: TestClient, sheets_dir: Path, test_settings: Settings
    ) -> None:
        test_settings.sheets_max_file_bytes = 1000
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", ("p.jpg", JPEG_BYTES + b"\x00" * 1000, "image/jpeg"))],
        )
        assert response.status_code == 413
        assert upload_dirs(sheets_dir) == []

    def test_upload_too_large(
        self, admin_client: TestClient, sheets_dir: Path, test_settings: Settings
    ) -> None:
        test_settings.sheets_max_upload_bytes = 500
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", (f"p{i}.jpg", JPEG_BYTES, "image/jpeg")) for i in range(3)],
        )
        assert response.status_code == 413
        assert upload_dirs(sheets_dir) == []

    def test_upload_too_large_by_content_length(
        self, admin_client: TestClient, sheets_dir: Path, test_settings: Settings
    ) -> None:
        test_settings.sheets_max_upload_bytes = 1000
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", ("p.jpg", JPEG_BYTES + b"\x00" * 200_000, "image/jpeg"))],
        )
        assert response.status_code == 413

    @pytest.mark.parametrize("extra", [1, 5])
    def test_too_many_files(
        self, admin_client: TestClient, sheets_dir: Path, test_settings: Settings, extra: int
    ) -> None:
        test_settings.sheets_max_files = 3
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", (f"p{i}.jpg", JPEG_BYTES, "image/jpeg")) for i in range(3 + extra)],
        )
        assert response.status_code == 422
        assert "at most 3" in response.json()["detail"]
        assert upload_dirs(sheets_dir) == []

    def test_max_files_allowed(
        self, admin_client: TestClient, sheets_dir: Path, test_settings: Settings
    ) -> None:
        test_settings.sheets_max_files = 3
        response = admin_client.post(
            "/test-course/admin/sheets",
            files=[("files", (f"p{i}.jpg", JPEG_BYTES, "image/jpeg")) for i in range(3)],
        )
        assert response.status_code == 200, response.text

    def test_disabled(self, admin_client: TestClient) -> None:
        response = admin_client.post(
            "/test-course/admin/sheets", files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))]
        )
        assert response.status_code == 503

    def test_write_failure_is_500_and_leaves_nothing(
        self, admin_client: TestClient, sheets_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_replace(src: str, dst: str) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(app.archive_files.os, "replace", failing_replace)
        response = admin_client.post(
            "/test-course/admin/sheets", files=[("files", ("p.jpg", JPEG_BYTES, "image/jpeg"))]
        )
        assert response.status_code == 500
        assert upload_dirs(sheets_dir) == []
