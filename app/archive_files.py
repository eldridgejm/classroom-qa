"""
Writing session archives to disk
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

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{session_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), FILE_MODE)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise

    # Make the rename itself durable
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return path
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)

    return path
