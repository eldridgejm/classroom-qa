"""Helpers for building distribution payloads shared across routes."""

from __future__ import annotations

from typing import Any

from app.models import QuestionType
from app.redis_client import RedisClient


def build_distribution(
    redis_client: RedisClient,
    course: str,
    question_id: str,
) -> dict[str, Any] | None:
    """Return distribution data for a question or None if metadata missing."""

    meta = redis_client.get_question_meta(course, question_id)
    if meta is None:
        return None

    qtype = QuestionType(meta["type"])
    options = meta.get("options")

    counts = redis_client.get_counts(course, question_id)
    normalized_counts: dict[str, int] = {k: v for k, v in counts.items()}

    if qtype == QuestionType.MCQ and options:
        for option in options:
            normalized_counts.setdefault(option, 0)

    if qtype == QuestionType.TF:
        normalized_counts.setdefault("true", 0)
        normalized_counts.setdefault("false", 0)

    total = sum(normalized_counts.values())
    percentages: dict[str, float] = {}

    if total > 0:
        for key, count in normalized_counts.items():
            percentages[key] = round((count / total) * 100, 2)
    else:
        for key in normalized_counts.keys():
            percentages[key] = 0.0

    return {
        "question_id": question_id,
        "type": qtype.value,
        "counts": normalized_counts,
        "total": total,
        "percentages": percentages,
        "options": options,
    }
