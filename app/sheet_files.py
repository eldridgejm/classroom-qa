"""
Storing uploaded attendance sheets on disk

Each upload is its own directory, <sheets_dir>/<course>/<UTC timestamp>-<id>/,
holding the files in upload order (page-01.jpg, page-02.jpg, ... for images,
scan-NN.pdf for PDFs, numbered in the same sequence) and, written last, a
manifest.json. A directory without manifest.json is an upload still being
written (or one that failed) and should be ignored by readers.
"""

import hashlib
import json
import logging
import os
import secrets
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.archive_files import DIR_MODE, atomic_write_bytes, fsync_dir

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class SheetType:
    """A file type accepted for sheet uploads"""

    mime: str
    ext: str

    @property
    def is_image(self) -> bool:
        return self.mime.startswith("image/")


JPEG = SheetType("image/jpeg", "jpg")
PNG = SheetType("image/png", "png")
HEIC = SheetType("image/heic", "heic")
PDF = SheetType("application/pdf", "pdf")

# ISO BMFF brands of HEIF images (not AVIF, which shares the container)
_HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx"}
_HEIF_GENERIC_BRANDS = {b"mif1", b"msf1"}


def sniff_sheet_type(data: bytes) -> SheetType | None:
    """
    Identify an uploaded file by its content (not its name or claimed type)

    Returns:
        The file's type, or None if it isn't a JPEG, PNG, HEIC, or PDF
    """
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"%PDF-"):
        return PDF
    if len(data) >= 16 and data[4:8] == b"ftyp":
        box_size = int.from_bytes(data[0:4], "big")
        major = data[8:12]
        end = min(len(data), box_size) if box_size >= 16 else 16
        compatible = {data[i : i + 4] for i in range(16, end - 3, 4)}
        if major in _HEIC_BRANDS:
            return HEIC
        if major in _HEIF_GENERIC_BRANDS and compatible & _HEIC_BRANDS:
            return HEIC
    return None


@dataclass(frozen=True)
class SheetFile:
    """One validated file of an upload"""

    data: bytes
    type: SheetType
    original_name: str | None = None


def _check_path_component(part: str) -> None:
    if not part or part in (".", "..") or "/" in part or "\\" in part or part.startswith("."):
        raise ValueError(f"Invalid path component for sheet upload: {part!r}")


def course_sheets_dir(sheets_dir: str | os.PathLike[str], course: str) -> Path:
    """Directory holding a course's uploads: <sheets_dir>/<course>"""
    _check_path_component(course)
    return Path(sheets_dir) / course


def new_upload_name(now: datetime) -> str:
    """Directory name for a new upload, e.g. 20261101T183012Z-1a2b3c4d (sorts by time)"""
    return f"{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def write_sheet_upload(
    sheets_dir: str | os.PathLike[str],
    course: str,
    files: list[SheetFile],
    lecture: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Store an upload as <sheets_dir>/<course>/<timestamp>-<id>/

    Every file is written atomically (temp file + rename) with mode 0640, the
    directory has mode 0750, and manifest.json is written last. If anything
    fails, the partial directory is removed and the error re-raised.

    Args:
        sheets_dir: Base directory for uploads
        course: Course slug
        files: The upload's files, in order (already validated)
        lecture: Lecture number, or None for "the next lecture"
        now: Upload time (default: now)

    Returns:
        The manifest
    """
    if not files:
        raise ValueError("An upload needs at least one file")

    now = now or datetime.now(UTC)
    course_dir = course_sheets_dir(sheets_dir, course)
    course_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)

    name = new_upload_name(now)
    upload_dir = course_dir / name
    upload_dir.mkdir(mode=DIR_MODE)
    fsync_dir(course_dir)

    try:
        entries = []
        for i, f in enumerate(files, start=1):
            stem = "page" if f.type.is_image else "scan"
            filename = f"{stem}-{i:02d}.{f.type.ext}"
            atomic_write_bytes(upload_dir / filename, f.data)
            entries.append(
                {
                    "name": filename,
                    "type": f.type.mime,
                    "bytes": len(f.data),
                    "sha256": hashlib.sha256(f.data).hexdigest(),
                    "original_name": (f.original_name or None) and f.original_name[:200],
                }
            )

        manifest = {
            "id": name,
            "uploaded_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "course": course,
            "lecture": lecture,
            "files": entries,
        }
        atomic_write_bytes(
            upload_dir / MANIFEST_NAME, (json.dumps(manifest, indent=2) + "\n").encode()
        )
    except BaseException:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    return manifest


def list_sheet_uploads(
    sheets_dir: str | os.PathLike[str], course: str, limit: int = 20
) -> list[dict[str, Any]]:
    """
    The course's most recent complete uploads (those with a manifest), newest first
    """
    course_dir = course_sheets_dir(sheets_dir, course)
    try:
        names = [
            entry.name
            for entry in os.scandir(course_dir)
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except FileNotFoundError:
        return []

    uploads = []
    for name in sorted(names, reverse=True):
        try:
            manifest = json.loads((course_dir / name / MANIFEST_NAME).read_text())
        except FileNotFoundError:
            continue  # incomplete
        except (OSError, ValueError):
            logger.warning("Unreadable sheet upload manifest in %s", course_dir / name)
            continue
        if isinstance(manifest, dict) and isinstance(manifest.get("files"), list):
            uploads.append(manifest)
        if len(uploads) >= limit:
            break
    return uploads
