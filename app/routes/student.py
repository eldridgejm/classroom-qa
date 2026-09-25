"""
Student routes for course participation
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, field_validator

import app.config
from app.auth import create_tsn_cookie, require_tsn, validate_tsn_format, verify_tsn_cookie
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


# Dependency to verify TSN authentication
def verify_tsn_auth(
    student_session: Annotated[str | None, Cookie()] = None,
) -> str:
    """Verify TSN authentication and return TSN"""
    return require_tsn(student_session, app.config.settings.secret_key)


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


@router.get("/{course}", response_class=HTMLResponse)
async def student_page(request: Request, course: str) -> Response:
    """
    Student page - shows TSN entry if not authenticated, main page if authenticated
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check if student has TSN cookie
    tsn_cookie = request.cookies.get("student_session")
    tsn = verify_tsn_cookie(tsn_cookie, app.config.settings.secret_key)

    if tsn is not None:
        # Check if session is live
        import redis
        redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
        redis_wrapper = RedisClient(redis_conn)
        session_is_live = redis_wrapper.is_session_live(course)

        # Check if there's a current question
        current_question = None
        student_answer = None

        if session_is_live:
            current_qid = redis_wrapper.get_current_question(course)
            if current_qid:
                question_meta = redis_wrapper.get_question_meta(course, current_qid)
                # Only show the question if it hasn't ended yet
                if question_meta and question_meta.get("ended_at") is None:
                    current_question = {
                        "id": current_qid,
                        "type": question_meta["type"],
                        "options": question_meta.get("options"),
                    }
                    # Get student's previous answer if any
                    response = redis_wrapper.get_response(course, current_qid, tsn)
                    if response:
                        student_answer = response.get("resp")

        redis_conn.close()

        # Show main student page
        return templates.TemplateResponse(
            request=request,
            name="student.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "tsn": tsn,
                "session_is_live": session_is_live,
                "current_question": current_question,
                "student_answer": student_answer,
            },
        )
    else:
        # Show TSN entry page
        return templates.TemplateResponse(
            request=request,
            name="tsn_entry.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": None,
            },
        )


@router.post("/{course}/enter-tsn")
async def enter_tsn(
    request: Request,
    course: str,
    tsn: str = Form(...),
) -> Response:
    """
    TSN entry endpoint - validates TSN format and sets cookie
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Validate TSN format (ignoring stray whitespace from copy/paste)
    tsn = tsn.strip()
    if not validate_tsn_format(tsn):
        # Return to TSN entry page with error
        return templates.TemplateResponse(
            request=request,
            name="tsn_entry.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": "Invalid TSN format. Must be exactly 9 digits (e.g., 123456789)",
            },
            status_code=400,
        )

    # Create TSN cookie
    cookie = create_tsn_cookie(tsn, app.config.settings.secret_key)

    # Redirect to student page (include root_path for subpath deployments)
    root_path = request.scope.get("root_path", "")
    response = RedirectResponse(
        url=f"{root_path}/{course}",
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

@router.post("/{course}/answer")
async def submit_answer(
    request: Request,
    course: str,
    tsn: Annotated[str, Depends(verify_tsn_auth)],
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
    counts = redis_client.submit_answer(course, qid, tsn, response_value)

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


@router.get("/{course}/results/{qid}")
async def get_shared_results(
    course: str,
    qid: str,
    tsn: Annotated[str, Depends(verify_tsn_auth)],
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

    response = redis_client.get_response(course, qid, tsn)
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


def strip_tsns_from_text(text: str) -> str:
    """Strip TSNs from text and replace with [TSN]"""
    import re
    # Match UCSD TSN format: exactly 9 digits
    pattern = r'\b\d{9}\b'
    return re.sub(pattern, '[TSN]', text)


@router.post("/{course}/ask")
async def ask_question(
    request: Request,
    course: str,
    tsn: Annotated[str, Depends(verify_tsn_auth)],
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
    allowed, retry_after = redis_client.check_ask_rate_limit(course, tsn)
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

    # Strip TSNs from question text
    sanitized_question = strip_tsns_from_text(question_text)

    # Submit question to Redis
    question_id = redis_client.submit_question(
        course=course,
        tsn=tsn,
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
            "tsn": tsn,
        },
    )

    return AskQuestionResponse(status="success", question_id=question_id)
