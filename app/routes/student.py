"""
Student routes for course participation
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

import app.config
from app.auth import create_pid_cookie, require_pid, validate_pid_format, verify_pid_cookie
from app.models import EventType, QuestionType
from app.redis_client import RedisClient
from app.services.distribution import build_distribution

router = APIRouter()

# Get template directory - works both in dev and when packaged
import pathlib
template_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(template_dir))


# Dependency to get Redis client
def get_redis_client() -> RedisClient:
    """Get Redis client instance"""
    import redis
    redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
    return RedisClient(redis_conn)


# Dependency to verify PID authentication
def verify_pid_auth(
    student_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Verify PID authentication and return PID"""
    return require_pid(student_session, app.config.settings.secret_key)


# Request/Response models

class AnswerSubmitRequest(BaseModel):
    """Request to submit an answer"""
    question_id: str
    response: str | bool | int | float

    @field_validator("response")
    @classmethod
    def validate_response_not_none(cls, v):
        """Validate that response is not None or empty"""
        if v is None:
            raise ValueError("Response cannot be None")
        if isinstance(v, str) and v == "":
            raise ValueError("Response cannot be empty string")
        return v


class AnswerSubmitResponse(BaseModel):
    """Response for answer submission"""
    status: str
    counts: dict[str, int] | None = None


class QuestionResultsResponse(BaseModel):
    """Response for viewing shared results"""
    question_id: str
    type: str
    counts: dict[str, int]
    total: int
    percentages: dict[str, float]
    options: list[str] | None = None
    your_answer: str | bool | float | None = None


@router.get("/c/{course}", response_class=HTMLResponse)
async def student_page(request: Request, course: str) -> Response:
    """
    Student page - shows PID entry if not authenticated, main page if authenticated
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check if student has PID cookie
    pid_cookie = request.cookies.get("student_session")
    pid = verify_pid_cookie(pid_cookie, app.config.settings.secret_key)

    if pid is not None:
        # Check if session is live
        import redis
        redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
        redis_wrapper = RedisClient(redis_conn)
        session_is_live = redis_wrapper.is_session_live(course)
        redis_conn.close()

        # Show main student page
        return templates.TemplateResponse(
            request=request,
            name="student.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "pid": pid,
                "session_is_live": session_is_live,
            },
        )
    else:
        # Show PID entry page
        return templates.TemplateResponse(
            request=request,
            name="pid_entry.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": None,
            },
        )


@router.post("/c/{course}/enter-pid")
async def enter_pid(
    request: Request,
    course: str,
    pid: str = Form(...),
) -> Response:
    """
    PID entry endpoint - validates PID format and sets cookie
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Validate PID format
    if not validate_pid_format(pid):
        # Return to PID entry page with error
        return templates.TemplateResponse(
            request=request,
            name="pid_entry.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": "Invalid PID format. Must be A followed by 8 digits (e.g., A12345678)",
            },
            status_code=400,
        )

    # Create PID cookie
    cookie = create_pid_cookie(pid, app.config.settings.secret_key)

    # Redirect to student page
    response = RedirectResponse(
        url=f"/c/{course}",
        status_code=303,
    )
    response.set_cookie(
        key="student_session",
        value=cookie,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=86400,  # 24 hours
    )

    return response


# Answer Submission Route

@router.post("/c/{course}/answer")
async def submit_answer(
    request: Request,
    course: str,
    pid: Annotated[str, Depends(verify_pid_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> AnswerSubmitResponse:
    """
    Submit an answer to a question (accepts both JSON and form data)
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Verify session is live
    if not redis_client.is_session_live(course):
        raise HTTPException(
            status_code=400,
            detail="No active session for this course",
        )

    # Parse request body based on Content-Type
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # Parse as JSON
        try:
            body = await request.json()
            qid = body.get("question_id")
            response_value = body.get("response")
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON body: {str(e)}",
            )
    else:
        # Parse as form data
        try:
            form = await request.form()
            qid = form.get("question_id")
            response_raw = form.get("response")

            # Try to parse response value (could be bool, number, or string)
            if response_raw is not None and response_raw.lower() == "true":
                response_value = True
            elif response_raw is not None and response_raw.lower() == "false":
                response_value = False
            elif response_raw is not None:
                # Try to parse as number
                try:
                    response_value = float(response_raw) if '.' in response_raw else int(response_raw)
                except (ValueError, TypeError, AttributeError):
                    # Keep as string
                    response_value = response_raw
            else:
                response_value = None
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid form data: {str(e)}",
            )

    # Validate required fields
    if qid is None:
        raise HTTPException(status_code=422, detail="Field 'question_id' is required")
    if response_value is None:
        raise HTTPException(status_code=422, detail="Field 'response' is required")
    if isinstance(response_value, str) and response_value == "":
        raise HTTPException(status_code=422, detail="Response cannot be empty string")

    # Get question metadata
    meta = redis_client.get_question_meta(course, qid)

    if meta is None:
        raise HTTPException(
            status_code=400,
            detail="Question not found or no active question",
        )

    # Check if question is still active (not ended)
    if meta.get("ended_at") is not None:
        raise HTTPException(
            status_code=400,
            detail="Question has ended and is no longer accepting answers",
        )

    # Validate response type matches question type
    question_type = QuestionType(meta["type"])

    if question_type == QuestionType.MCQ:
        # MCQ expects string response
        if not isinstance(response_value, str):
            raise HTTPException(
                status_code=400,
                detail="MCQ questions require a string response (e.g., 'A', 'B', 'C', 'D')",
            )
        # Validate option is valid
        options = meta.get("options", [])
        if response_value not in options:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid option '{response_value}'. Valid options: {', '.join(options)}",
            )

    elif question_type == QuestionType.TF:
        # T/F expects boolean response
        if not isinstance(response_value, bool):
            raise HTTPException(
                status_code=400,
                detail="True/False questions require a boolean response (true or false)",
            )

    elif question_type == QuestionType.NUMERIC:
        # Numeric expects number or string (for fractions like "1/2")
        if not isinstance(response_value, (int, float, str)):
            raise HTTPException(
                status_code=400,
                detail="Numeric questions require a number or string response",
            )

    # Submit answer using atomic Lua script
    counts = redis_client.submit_answer(course, qid, pid, response_value)

    # Publish SSE event with updated counts (admin only)
    redis_client.publish_event(
        course,
        EventType.COUNTS_UPDATED,
        {
            "question_id": qid,
            "counts": counts,
        },
    )

    return AnswerSubmitResponse(status="submitted", counts=counts)


@router.get("/c/{course}/results/{qid}")
async def get_shared_results(
    course: str,
    qid: str,
    pid: Annotated[str, Depends(verify_pid_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> QuestionResultsResponse:
    """Return shared distribution data along with the student's answer."""

    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = redis_client.get_question_meta(course, qid)
    if meta is None:
        raise HTTPException(status_code=404, detail="Question not found")

    if not meta.get("results_shared"):
        raise HTTPException(status_code=404, detail="Results not available")

    distribution = build_distribution(redis_client, course, qid)
    if distribution is None:
        raise HTTPException(status_code=404, detail="Question metadata not found")

    response = redis_client.get_response(course, qid, pid)
    your_answer = None
    if response is not None:
        your_answer = response.get("resp")

    return QuestionResultsResponse(**distribution, your_answer=your_answer)

# Student Q&A Routes


class AskQuestionRequest(BaseModel):
    """Request to submit a student question"""
    question: str

    @field_validator("question")
    @classmethod
    def validate_question_length(cls, v):
        """Validate question length (max 1000 chars)"""
        if len(v) > 1000:
            raise ValueError("Question must be 1000 characters or less")
        if len(v.strip()) == 0:
            raise ValueError("Question cannot be empty")
        return v


class AskQuestionResponse(BaseModel):
    """Response for question submission"""
    status: str
    question_id: str


class RateLimitResponse(BaseModel):
    """Response when rate limited"""
    detail: str
    retry_after: int


def strip_pids_from_text(text: str) -> str:
    """Strip PIDs from text and replace with [PID]"""
    import re
    # Match UCSD PID format: A followed by 8 digits
    pattern = r'\bA\d{8}\b'
    return re.sub(pattern, '[PID]', text)


@router.post("/c/{course}/ask")
async def ask_question(
    request: Request,
    course: str,
    pid: Annotated[str, Depends(verify_pid_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> AskQuestionResponse:
    """
    Submit a student question
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Verify session is live
    if not redis_client.is_session_live(course):
        raise HTTPException(
            status_code=400,
            detail="Session is not active. Questions can only be submitted during live sessions.",
        )

    # Check rate limit
    allowed, retry_after = redis_client.check_ask_rate_limit(course, pid)
    if not allowed:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"Rate limit exceeded. Please wait {retry_after} seconds before asking another question.",
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # Parse request body (form data)
    try:
        form = await request.form()
        question_text = form.get("question")
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid form data: {str(e)}",
        )

    # Validate question field exists
    if question_text is None:
        raise HTTPException(status_code=422, detail="Field 'question' is required")

    # Validate question length
    if len(question_text) > 1000:
        raise HTTPException(
            status_code=422,
            detail="Question must be 1000 characters or less",
        )

    if len(question_text.strip()) == 0:
        raise HTTPException(status_code=422, detail="Question cannot be empty")

    # Strip PIDs from question text
    sanitized_question = strip_pids_from_text(question_text)

    # Submit question to Redis
    question_id = redis_client.submit_question(
        course=course,
        pid=pid,
        question=sanitized_question,
        ttl=1800,  # 30 minutes
    )

    # Publish SSE event for new question (admin only)
    redis_client.publish_event(
        course,
        EventType.NEW_QUESTION,
        {
            "question_id": question_id,
            "question": sanitized_question,
            "pid": pid,
        },
    )

    return AskQuestionResponse(status="success", question_id=question_id)
