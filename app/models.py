"""
Pydantic models for the application
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class QuestionType(str, Enum):
    """Type of question"""

    MCQ = "mcq"  # Multiple choice
    TF = "tf"  # True/False
    NUMERIC = "numeric"  # Numeric answer


class QuestionMeta(BaseModel):
    """Metadata for a question"""

    id: str
    type: QuestionType
    options: list[str] | None = None
    started_at: datetime
    ended_at: datetime | None = None
    results_shared: bool = False
    results_shared_at: datetime | None = None


class Response(BaseModel):
    """A student's response to a question"""

    pid: str
    timestamp: datetime
    value: str | bool | float


class SessionState(BaseModel):
    """State of a course session"""

    course: str
    is_live: bool
    current_question_id: str | None = None


# SSE Event Models


class EventType(str, Enum):
    """Types of SSE events"""

    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    QUESTION_STARTED = "question_started"
    QUESTION_STOPPED = "question_stopped"
    COUNTS_UPDATED = "counts_updated"
    NEW_QUESTION = "new_question"  # Student asked a question
    ESCALATION = "escalation"
    RESULTS_PUBLISHED = "results_published"


class QuestionStartedEvent(BaseModel):
    """Event when a question is started"""

    question_id: str
    type: QuestionType
    options: list[str] | None = None


class QuestionStoppedEvent(BaseModel):
    """Event when a question is stopped"""

    question_id: str


class CountsUpdatedEvent(BaseModel):
    """Event when answer counts are updated"""

    question_id: str
    counts: dict[str, int]


class EscalationEvent(BaseModel):
    """Event when student questions are escalated"""

    summary: str
    count: int
