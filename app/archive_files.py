"""
Writing session archives (and other files other local users read) to disk
"""

import os
import tempfile
from pathlib import Path

# Archive files are group-readable so that another local user (e.g. a CI
# runner in the service's group) can read them. Directories get the matching
# mode; on a setgid parent directory, new subdirectories inherit the group.
FILE_MODE = 0o640
DIR_MODE = 0o750


def archive_file_path(archive_dir: str | os.PathLike[str], course: str, session_id: str) -> Path:
    """Path of the archive file for a session: <archive_dir>/<course>/<session_id>.json"""
    for part in (course, session_id):
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise ValueError(f"Invalid path component for archive file: {part!r}")
    return Path(archive_dir) / course / f"{session_id}.json"


def write_archive_file(
    archive_dir: str | os.PathLike[str], course: str, session_id: str, content: str
) -> Path:
    """
    Atomically write an archive's JSON to <archive_dir>/<course>/<session_id>.json

    The content is written to a temporary file in the same directory, flushed to
    disk, and then renamed into place, so readers never see a partial file.
    Temporary files start with "." and end in ".tmp", so they never match
    "*.json".

    Args:
        archive_dir: Base directory for archives
        course: Course slug
        session_id: Archived session ID
        content: JSON text to write

    Returns:
        Path of the written file
    """
    path = archive_file_path(archive_dir, course, session_id)
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    atomic_write_bytes(path, content.encode("utf-8"))
    return path


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """
    Atomically write data to path, with mode FILE_MODE

    The data is written to a temporary file in the same directory (named
    ".<name>.<random>.tmp"), flushed to disk, and renamed into place, so a
    reader never sees a partial file at path. The parent directory must exist.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), FILE_MODE)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

    fsync_dir(path.parent)


def fsync_dir(directory: Path) -> None:
    """Best-effort fsync of a directory, to make renames and creations in it durable"""
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
