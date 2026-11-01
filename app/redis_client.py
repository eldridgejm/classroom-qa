"""
Redis client for managing session state, questions, and responses
"""

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import redis

from app.archive_files import write_archive_file
from app.models import EventType, QuestionType

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client wrapper for all application operations"""

    def __init__(self, redis_client: redis.Redis) -> None:
        """
        Initialize Redis client wrapper

        Args:
            redis_client: Redis client instance
        """
        self.redis = redis_client
        self._load_lua_scripts()

    def _load_lua_scripts(self) -> None:
        """Load and register Lua scripts for atomic operations"""
        # Lua script for atomic answer update
        # Decrements old answer count, increments new answer count
        self.atomic_answer_script = self.redis.register_script(
            """
            local responses_key = KEYS[1]
            local counts_key = KEYS[2]
            local tsn = ARGV[1]
            local new_answer_json = ARGV[2]
            local new_answer_value = ARGV[3]

            -- Get old answer if exists
            local old_answer_json = redis.call('HGET', responses_key, tsn)
            if old_answer_json then
                local old_data = cjson.decode(old_answer_json)
                local old_value = tostring(old_data.resp)
                redis.call('HINCRBY', counts_key, old_value, -1)

                -- Remove count if it reaches 0
                local new_count = tonumber(redis.call('HGET', counts_key, old_value))
                if new_count and new_count <= 0 then
                    redis.call('HDEL', counts_key, old_value)
                end
            end

            -- Set new answer
            redis.call('HSET', responses_key, tsn, new_answer_json)
            redis.call('HINCRBY', counts_key, new_answer_value, 1)

            return redis.call('HGETALL', counts_key)
            """
        )

    # Key generation helpers

    def session_key(self, course: str) -> str:
        """Generate Redis key for session state"""
        return f"course:{course}:session:live"

    def current_qid_key(self, course: str) -> str:
        """Generate Redis key for current question ID"""
        return f"course:{course}:current_qid"

    def question_meta_key(self, course: str, qid: str) -> str:
        """Generate Redis key for question metadata"""
        return f"course:{course}:q:{qid}:meta"

    def question_responses_key(self, course: str, qid: str) -> str:
        """Generate Redis key for question responses"""
        return f"course:{course}:q:{qid}:responses"

    def question_counts_key(self, course: str, qid: str) -> str:
        """Generate Redis key for question counts"""
        return f"course:{course}:q:{qid}:counts"

    def numeric_cache_key(self, course: str, qid: str) -> str:
        """Generate Redis key for numeric answer cache"""
        return f"course:{course}:q:{qid}:numeric_cache"

    def events_channel_key(self, course: str) -> str:
        """Generate Redis pub/sub channel key for events"""
        return f"course:{course}:events"

    def archive_key(self, course: str, session_id: str) -> str:
        """Generate Redis key for archived session"""
        return f"course:{course}:archive:{session_id}"

    def question_key(self, course: str, question_id: str) -> str:
        """Generate Redis key for student question"""
        return f"course:{course}:question:{question_id}"

    def rate_limit_key(self, course: str, tsn: str) -> str:
        """Generate Redis key for Ask rate limiting"""
        return f"course:{course}:ratelimit:ask:{tsn}"

    def last_activity_key(self, course: str) -> str:
        """Generate Redis key for the time of the live session's last activity"""
        return f"course:{course}:last_activity"

    def stop_lock_key(self, course: str) -> str:
        """
        Generate Redis key for the lock held while stopping a session

        Deliberately outside the course:{course}:* namespace, which
        clear_session_data deletes while the lock is held.
        """
        return f"lock:course:{course}:stop"

    def admin_remember_version_key(self, course: str) -> str:
        """
        Generate Redis key for the course's "remember this device" token version

        Deliberately outside the course:{course}:* namespace, which
        clear_session_data deletes (and apply_ttl_to_course_keys expires) when
        a session stops: losing the counter would un-revoke old tokens.
        """
        return f"admin:course:{course}:remember_version"

    # Remembered admin devices

    def get_admin_remember_version(self, course: str) -> int:
        """
        Current version of the course's remembered-device tokens (0 if never revoked)

        Only tokens carrying this version are accepted.
        """
        value = cast(str | bytes | None, self.redis.get(self.admin_remember_version_key(course)))
        if value is None:
            return 0
        try:
            return int(value)
        except ValueError:
            return 0

    def revoke_admin_remembered_devices(self, course: str) -> int:
        """
        Revoke every remembered device for the course by bumping the version

        Returns:
            The new version
        """
        return int(cast(int, self.redis.incr(self.admin_remember_version_key(course))))

    # Activity tracking

    def touch_activity(self, course: str) -> None:
        """
        Record that something happened in the course's session just now
        (used to detect idle sessions)

        Args:
            course: Course slug
        """
        self.redis.set(self.last_activity_key(course), repr(time.time()))

    def get_last_activity(self, course: str) -> float | None:
        """
        Get the time (Unix epoch seconds) of the session's last activity

        Args:
            course: Course slug

        Returns:
            Timestamp, or None if no activity has been recorded
        """
        value = cast(str | bytes | None, self.redis.get(self.last_activity_key(course)))
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    # Session operations

    def clear_session_data(self, course: str) -> None:
        """
        Clear all current session data (questions, responses, counts, etc.)
        Does not affect archived sessions.

        Args:
            course: Course slug
        """
        # Find all current session keys (excludes archives)
        pattern = f"course:{course}:*"
        archive_pattern = f"course:{course}:archive:*"

        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                # Skip archive keys
                key_str = key if isinstance(key, str) else key.decode()
                if not key_str.startswith(f"course:{course}:archive:"):
                    self.redis.delete(key)

            if cursor == 0:
                break

    def start_session(self, course: str) -> None:
        """
        Start a session for a course
        Clears any old session data first.

        Args:
            course: Course slug
        """
        # Clear old session data
        self.clear_session_data(course)

        # Start new session
        key = self.session_key(course)
        self.redis.set(key, "1")
        self.touch_activity(course)

    def stop_session(
        self,
        course: str,
        ttl: int | None = None,
        archive_dir: str | os.PathLike[str] | None = None,
    ) -> str:
        """
        Stop a session for a course
        Archives current session data and clears it.

        Args:
            course: Course slug
            ttl: Optional TTL in seconds for archived session (default: 172800 = 48 hours)
            archive_dir: If set, also write the archive to <archive_dir>/<course>/<id>.json

        Returns:
            Session ID of archived session
        """
        # Default TTL to 48 hours if not specified
        if ttl is None:
            ttl = 172800

        # Archive current session
        session_id = self.archive_session(course, ttl, archive_dir=archive_dir)

        # Clear current session data
        self.clear_session_data(course)

        return session_id

    def is_session_live(self, course: str) -> bool:
        """
        Check if a session is live

        Args:
            course: Course slug

        Returns:
            True if session is live, False otherwise
        """
        key = self.session_key(course)
        value = self.redis.get(key)
        # Session is live if value is "1"
        if value is None:
            return False
        if isinstance(value, bytes):
            return value.decode() == "1"
        return str(value) == "1"

    # Current question operations

    def get_current_question(self, course: str) -> str | None:
        """
        Get the current question ID for a course

        Args:
            course: Course slug

        Returns:
            Question ID if set, None otherwise
        """
        key = self.current_qid_key(course)
        qid = self.redis.get(key)
        # Handle both decoded and bytes responses
        if qid is None:
            return None
        if isinstance(qid, bytes):
            return qid.decode()
        return str(qid)

    def set_current_question(self, course: str, qid: str) -> None:
        """
        Set the current question ID for a course

        Args:
            course: Course slug
            qid: Question ID
        """
        key = self.current_qid_key(course)
        self.redis.set(key, qid)

    def clear_current_question(self, course: str) -> None:
        """
        Clear the current question ID for a course

        Args:
            course: Course slug
        """
        key = self.current_qid_key(course)
        self.redis.delete(key)

    def get_active_question(self, course: str) -> dict[str, Any] | None:
        """
        Get metadata for the current question if it is still accepting answers

        Args:
            course: Course slug

        Returns:
            Question metadata dict, or None if no question is active
        """
        qid = self.get_current_question(course)
        if qid is None:
            return None

        meta = self.get_question_meta(course, qid)
        if meta is None or meta.get("ended_at") is not None:
            return None

        return meta

    # Question metadata operations

    def create_question(
        self,
        course: str,
        qtype: QuestionType,
        options: list[str] | None = None,
    ) -> str:
        """
        Create a new question
        Ends the previous question first if it is still active.

        Args:
            course: Course slug
            qtype: Question type
            options: Optional list of options for MCQ questions

        Returns:
            Question ID
        """
        # End the previous question so it doesn't stay open forever
        previous_qid = self.get_current_question(course)
        if previous_qid is not None:
            self.stop_question(course, previous_qid)

        # Generate unique question ID
        qid = f"q-{uuid.uuid4()}"

        # Create metadata
        meta = {
            "id": qid,
            "type": qtype.value,
            "options": options,
            "started_at": datetime.now(UTC).isoformat(),
            "ended_at": None,
            "results_shared": False,
            "results_shared_at": None,
        }

        # Store metadata
        key = self.question_meta_key(course, qid)
        self.redis.set(key, json.dumps(meta))

        # Set as current question
        self.set_current_question(course, qid)
        self.touch_activity(course)

        return qid

    def get_question_meta(self, course: str, qid: str) -> dict[str, Any] | None:
        """
        Get question metadata

        Args:
            course: Course slug
            qid: Question ID

        Returns:
            Question metadata dict or None if not found
        """
        key = self.question_meta_key(course, qid)
        data = self.redis.get(key)

        if data is None:
            return None

        return cast(dict[str, Any], json.loads(data))

    def stop_question(self, course: str, qid: str) -> None:
        """
        Stop a question

        Args:
            course: Course slug
            qid: Question ID
        """
        # Update metadata with ended_at timestamp
        meta = self.get_question_meta(course, qid)
        if meta is not None and meta.get("ended_at") is None:
            meta["ended_at"] = datetime.now(UTC).isoformat()
            key = self.question_meta_key(course, qid)
            self.redis.set(key, json.dumps(meta))

        # Clear current question if this is the current one
        current_qid = self.get_current_question(course)
        if current_qid == qid:
            self.clear_current_question(course)

        self.touch_activity(course)

    def mark_results_shared(self, course: str, qid: str) -> bool:
        """Mark a question's results as shared with students."""

        meta = self.get_question_meta(course, qid)
        if meta is None:
            return False

        if meta.get("results_shared"):
            return False

        meta["results_shared"] = True
        meta["results_shared_at"] = datetime.now(UTC).isoformat()

        key = self.question_meta_key(course, qid)
        self.redis.set(key, json.dumps(meta))
        return True

    # Response operations

    def submit_answer(
        self,
        course: str,
        qid: str,
        tsn: str,
        answer: str | bool | float,
    ) -> dict[str, int]:
        """
        Submit an answer atomically (updates counts)

        Args:
            course: Course slug
            qid: Question ID
            tsn: Student TSN
            answer: Answer value

        Returns:
            Updated counts dict
        """
        responses_key = self.question_responses_key(course, qid)
        counts_key = self.question_counts_key(course, qid)

        # Prepare answer data
        answer_data = {
            "ts": datetime.now(UTC).isoformat(),
            "resp": answer,
        }
        answer_json = json.dumps(answer_data)

        # Convert answer to string for counting
        if isinstance(answer, bool):
            answer_value = str(answer).lower()
        else:
            answer_value = str(answer)

        # Execute atomic update script
        result = self.atomic_answer_script(
            keys=[responses_key, counts_key],
            args=[tsn, answer_json, answer_value],
        )
        self.touch_activity(course)

        # Convert result to dict
        counts = {}
        if result:
            for i in range(0, len(result), 2):
                key = result[i].decode() if isinstance(result[i], bytes) else result[i]
                value = int(result[i + 1])
                counts[key] = value

        return counts

    def get_response(
        self, course: str, qid: str, tsn: str
    ) -> dict[str, Any] | None:
        """
        Get a student's response to a question

        Args:
            course: Course slug
            qid: Question ID
            tsn: Student TSN

        Returns:
            Response data dict or None if not found
        """
        key = self.question_responses_key(course, qid)
        data = self.redis.hget(key, tsn)

        if data is None:
            return None

        return cast(dict[str, Any], json.loads(data))

    def get_all_responses(self, course: str, qid: str) -> dict[str, dict[str, Any]]:
        """
        Get all responses for a question

        Args:
            course: Course slug
            qid: Question ID

        Returns:
            Dict mapping TSN to response data
        """
        key = self.question_responses_key(course, qid)
        data = self.redis.hgetall(key)

        responses = {}
        for tsn_bytes, response_bytes in data.items():
            tsn = tsn_bytes.decode() if isinstance(tsn_bytes, bytes) else tsn_bytes
            response_json = (
                response_bytes.decode()
                if isinstance(response_bytes, bytes)
                else response_bytes
            )
            responses[tsn] = json.loads(response_json)

        return responses

    # Count operations

    def get_counts(self, course: str, qid: str) -> dict[str, int]:
        """
        Get answer counts for a question

        Args:
            course: Course slug
            qid: Question ID

        Returns:
            Dict mapping answer value to count
        """
        key = self.question_counts_key(course, qid)
        data = self.redis.hgetall(key)

        counts = {}
        for answer_bytes, count_bytes in data.items():
            answer = (
                answer_bytes.decode() if isinstance(answer_bytes, bytes) else answer_bytes
            )
            count = int(count_bytes)
            counts[answer] = count

        return counts

    # Bulk operations

    def get_all_question_ids(self, course: str) -> list[str]:
        """
        Get all question IDs for a course

        Args:
            course: Course slug

        Returns:
            List of question IDs
        """
        pattern = f"course:{course}:q:*:meta"
        question_ids = []

        # Use SCAN to find all question metadata keys
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                # Extract question ID from key
                # Key format: course:{course}:q:{id}:meta
                parts = key.split(":")
                if len(parts) >= 4:
                    qid = parts[3]
                    question_ids.append(qid)

            if cursor == 0:
                break

        return question_ids

    def apply_ttl_to_course_keys(self, course: str, ttl: int) -> None:
        """
        Apply TTL to all keys for a course

        Args:
            course: Course slug
            ttl: TTL in seconds
        """
        pattern = f"course:{course}:*"

        # Use SCAN to find all keys for this course
        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                self.redis.expire(key, ttl)

            if cursor == 0:
                break

    # Pub/sub operations

    def publish_event(
        self, course: str, event_type: EventType, data: dict[str, Any]
    ) -> None:
        """
        Publish an event to the course's pub/sub channel

        Args:
            course: Course slug
            event_type: Type of event
            data: Event data
        """
        channel = self.events_channel_key(course)
        message = {"event": event_type.value, "data": data}
        self.redis.publish(channel, json.dumps(message))

    # Archive operations

    def archive_session(
        self,
        course: str,
        ttl: int = 172800,
        archive_dir: str | os.PathLike[str] | None = None,
    ) -> str:
        """
        Archive current session data

        The archive is stored in Redis with the given TTL. If archive_dir is set,
        the same JSON is also written (atomically, permanently) to
        <archive_dir>/<course>/<session_id>.json. A failure to write the file is
        logged but does not prevent archiving to Redis.

        Args:
            course: Course slug
            ttl: TTL in seconds for archived session (default: 172800 = 48 hours)
            archive_dir: Optional directory to also write the archive file to

        Returns:
            Session ID of archived session
        """
        # Generate session ID (timestamp + short UUID)
        timestamp = int(datetime.now(UTC).timestamp())
        short_uuid = str(uuid.uuid4())[:8]
        session_id = f"arch-{timestamp}-{short_uuid}"

        # Collect session data
        started_at = None
        stopped_at = datetime.now(UTC).isoformat()

        # Get all questions
        question_ids = self.get_all_question_ids(course)
        questions_data = []

        for qid in question_ids:
            # Get question metadata
            meta = self.get_question_meta(course, qid)
            if meta is None:
                continue

            # Capture started_at from first question
            if started_at is None and "started_at" in meta:
                started_at = meta["started_at"]

            # Get all responses
            all_responses = self.get_all_responses(course, qid)

            # Format responses
            formatted_responses = {}
            for tsn, response_data in all_responses.items():
                formatted_responses[tsn] = {
                    "timestamp": response_data["ts"],
                    "response": response_data["resp"],
                }

            # Build question export object
            question_export = {
                "question_id": qid,
                "type": meta["type"],
                "responses": formatted_responses,
            }

            # Add options for MCQ
            if "options" in meta and meta["options"] is not None:
                question_export["options"] = meta["options"]

            # Add timestamps
            if "started_at" in meta:
                question_export["started_at"] = meta["started_at"]
            if "ended_at" in meta:
                question_export["ended_at"] = meta["ended_at"]

            questions_data.append(question_export)

        # Build archive data
        archive_data = {
            "session_id": session_id,
            "started_at": started_at,
            "stopped_at": stopped_at,
            "questions": questions_data,
        }

        # Store archive
        archive_json = json.dumps(archive_data)
        key = self.archive_key(course, session_id)
        self.redis.set(key, archive_json)
        self.redis.expire(key, ttl)

        # Also keep a permanent copy on disk
        if archive_dir:
            try:
                path = write_archive_file(archive_dir, course, session_id, archive_json)
                logger.info("Wrote archive %s", path)
            except Exception:
                logger.exception(
                    "Failed to write archive %s for course %s to %s "
                    "(it is still archived in Redis)",
                    session_id,
                    course,
                    archive_dir,
                )

        return session_id

    def get_archived_sessions(self, course: str) -> list[dict[str, Any]]:
        """
        Get list of archived sessions (metadata only)

        Args:
            course: Course slug

        Returns:
            List of archive metadata dicts (session_id, started_at, stopped_at, question_count)
        """
        pattern = f"course:{course}:archive:*"
        archives = []

        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                # Get archive data
                data = self.redis.get(key)
                if data is None:
                    continue

                archive = cast(dict[str, Any], json.loads(data))

                # Extract metadata
                metadata = {
                    "session_id": archive["session_id"],
                    "started_at": archive.get("started_at"),
                    "stopped_at": archive.get("stopped_at"),
                    "question_count": len(archive.get("questions", [])),
                }

                archives.append(metadata)

            if cursor == 0:
                break

        # Sort by stopped_at (most recent first)
        archives.sort(
            key=lambda x: x.get("stopped_at") or "",
            reverse=True,
        )

        return archives

    def get_archived_session(
        self, course: str, session_id: str
    ) -> dict[str, Any] | None:
        """
        Get full archived session data

        Args:
            course: Course slug
            session_id: Session ID

        Returns:
            Archive data dict or None if not found
        """
        key = self.archive_key(course, session_id)
        data = self.redis.get(key)

        if data is None:
            return None

        return cast(dict[str, Any], json.loads(data))

    # Student question operations

    def submit_question(
        self,
        course: str,
        tsn: str,
        question: str,
        ttl: int = 1800,
    ) -> str:
        """
        Submit a student question

        Args:
            course: Course slug
            tsn: Student TSN
            question: Question text (TSN already stripped)
            ttl: TTL in seconds (default: 1800 = 30 minutes)

        Returns:
            Question ID
        """
        # Generate question ID with timestamp for sorting
        timestamp = int(datetime.now(UTC).timestamp() * 1000)  # milliseconds
        short_uuid = str(uuid.uuid4())[:8]
        question_id = f"q-{timestamp}-{short_uuid}"

        # Create question data
        question_data = {
            "question_id": question_id,
            "tsn": tsn,
            "question": question,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Store question
        key = self.question_key(course, question_id)
        self.redis.set(key, json.dumps(question_data))
        self.redis.expire(key, ttl)
        self.touch_activity(course)

        return question_id

    def get_question(self, course: str, question_id: str) -> dict[str, Any] | None:
        """
        Get a student question

        Args:
            course: Course slug
            question_id: Question ID

        Returns:
            Question data dict or None if not found
        """
        key = self.question_key(course, question_id)
        data = self.redis.get(key)

        if data is None:
            return None

        return cast(dict[str, Any], json.loads(data))

    def get_all_questions(self, course: str) -> list[dict[str, Any]]:
        """
        Get all questions for a course

        Args:
            course: Course slug

        Returns:
            List of question dicts, sorted by timestamp (newest first)
        """
        pattern = f"course:{course}:question:*"
        questions = []

        cursor = 0
        while True:
            cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                data = self.redis.get(key)
                if data is None:
                    continue

                question = cast(dict[str, Any], json.loads(data))
                questions.append(question)

            if cursor == 0:
                break

        # Sort by timestamp (newest first)
        questions.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return questions

    def check_ask_rate_limit(self, course: str, tsn: str, window: int = 10) -> tuple[bool, int]:
        """
        Check if a student can ask a question (rate limiting)

        Args:
            course: Course slug
            tsn: Student TSN
            window: Rate limit window in seconds (default: 10)

        Returns:
            Tuple of (allowed, retry_after_seconds)
        """
        key = self.rate_limit_key(course, tsn)

        # Check if key exists
        if self.redis.exists(key):
            # Rate limited
            ttl = self.redis.ttl(key)
            return (False, max(0, ttl))

        # Not rate limited, set the key with TTL
        self.redis.set(key, "1", ex=window)
        return (True, 0)

    def delete_question(self, course: str, question_id: str) -> bool:
        """
        Delete a student question (dismiss)

        Args:
            course: Course slug
            question_id: Question ID

        Returns:
            True if question was deleted, False if not found
        """
        key = self.question_key(course, question_id)
        result = self.redis.delete(key)
        return result > 0
