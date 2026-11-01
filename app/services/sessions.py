"""
Stopping sessions, by hand (the admin's stop button) or automatically when idle

Both paths go through stop_session/stop_session_if_idle here, which hold a
per-course Redis lock while archiving so that concurrent stops (two workers,
or the idle checker racing the stop button) never archive a session twice.
"""

import logging
import time

from redis.exceptions import LockError
from redis.lock import Lock

from app.config import Settings
from app.models import EventType
from app.redis_client import RedisClient

logger = logging.getLogger(__name__)

# Archiving takes milliseconds; the lock expires on its own if a process dies
# while holding it.
STOP_LOCK_TIMEOUT = 60
# How long the stop button waits for another in-progress stop to finish
STOP_LOCK_WAIT = 10


class StopInProgressError(Exception):
    """Another process is stopping this course's session and didn't finish in time"""


def _archive_and_publish(redis_client: RedisClient, course: str, settings: Settings) -> str:
    """Archive and clear the live session, then tell connected pages it stopped"""
    session_id = redis_client.stop_session(
        course,
        ttl=settings.session_ttl,
        archive_dir=settings.archive_dir or None,
    )
    redis_client.publish_event(course, EventType.SESSION_STOPPED, {})
    return session_id


def _release(lock: Lock, course: str) -> None:
    try:
        lock.release()
    except LockError:
        logger.warning("Stop lock for course %s expired before it was released", course)


def stop_session(redis_client: RedisClient, course: str, settings: Settings) -> str | None:
    """
    Stop a course's session (what the admin "stop session" button does)

    Archives the live session (to Redis and, if configured, to disk), clears it,
    and publishes SESSION_STOPPED. If no session is live (e.g. it was just
    stopped by someone else), nothing is archived, but SESSION_STOPPED is still
    published so that open pages catch up.

    Returns:
        The archived session's ID, or None if no session was live

    Raises:
        StopInProgressError: if another stop held the lock for too long
    """
    lock = redis_client.redis.lock(redis_client.stop_lock_key(course), timeout=STOP_LOCK_TIMEOUT)
    if not lock.acquire(blocking=True, blocking_timeout=STOP_LOCK_WAIT):
        raise StopInProgressError(course)

    try:
        if not redis_client.is_session_live(course):
            redis_client.publish_event(course, EventType.SESSION_STOPPED, {})
            return None
        return _archive_and_publish(redis_client, course, settings)
    finally:
        _release(lock, course)


def stop_session_if_idle(
    redis_client: RedisClient,
    course: str,
    settings: Settings,
    now: float | None = None,
) -> str | None:
    """
    Stop a course's session if it has had no activity for settings.idle_session_timeout

    Stops it exactly like the stop button does. A live session with no recorded
    activity (e.g. one started before activity tracking existed) has its clock
    started now instead.

    Returns:
        The archived session's ID, or None if nothing was stopped
    """
    timeout = settings.idle_session_timeout
    if timeout <= 0 or not redis_client.is_session_live(course):
        return None

    # Don't wait: if someone else is stopping it, there's nothing to do
    lock = redis_client.redis.lock(redis_client.stop_lock_key(course), timeout=STOP_LOCK_TIMEOUT)
    if not lock.acquire(blocking=False):
        return None

    try:
        # Re-check under the lock
        if not redis_client.is_session_live(course):
            return None

        if now is None:
            now = time.time()

        last_activity = redis_client.get_last_activity(course)
        if last_activity is None:
            redis_client.touch_activity(course)
            return None
        if now - last_activity < timeout:
            return None

        session_id = _archive_and_publish(redis_client, course, settings)
        logger.warning(
            "Auto-stopped session for course %s after %d s without activity (archived as %s)",
            course,
            int(now - last_activity),
            session_id,
        )
        return session_id
    finally:
        _release(lock, course)


def stop_idle_sessions(
    redis_client: RedisClient, settings: Settings, now: float | None = None
) -> list[tuple[str, str]]:
    """
    Check every configured course and stop its session if idle

    Returns:
        (course, session_id) for each session that was stopped
    """
    stopped = []
    for course in settings.load_courses():
        try:
            session_id = stop_session_if_idle(redis_client, course, settings, now=now)
        except Exception:
            logger.exception("Failed to check course %s for an idle session", course)
            continue
        if session_id is not None:
            stopped.append((course, session_id))
    return stopped
