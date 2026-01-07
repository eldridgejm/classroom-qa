"""
Admin routes for course management
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

import app.config
from app.auth import create_admin_cookie, require_admin, verify_admin_cookie
from app.models import EventType, QuestionType
from app.redis_client import RedisClient
from app.services.distribution import build_distribution

router = APIRouter()

# Get template directory - works both in dev and when packaged
import pathlib
template_dir = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(template_dir))


# Add custom Jinja2 filters
def format_timestamp(iso_timestamp: str | None) -> str:
    """Format ISO timestamp for display"""
    if not iso_timestamp:
        return "N/A"

    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %I:%M:%S %p")
    except (ValueError, AttributeError):
        return iso_timestamp


templates.env.filters["format_timestamp"] = format_timestamp


# Dependency to get Redis client
def get_redis_client() -> RedisClient:
    """Get Redis client instance"""
    import redis
    redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
    return RedisClient(redis_conn)


# Dependency to verify admin authentication
def verify_admin_auth(
    course: str,
    admin_session: Annotated[str | None, Cookie()] = None,
) -> None:
    """Verify admin authentication"""
    require_admin(admin_session, course, app.config.settings)


# Request/Response models

class QuestionCreateRequest(BaseModel):
    """Request to create a new question"""
    type: QuestionType
    options: list[str] | None = None

    @field_validator("type")
    @classmethod
    def validate_type_and_options(cls, v: QuestionType, info) -> QuestionType:
        """Validate that MCQ questions have options"""
        # This validator runs after options is set, so we can check both
        return v

    def model_post_init(self, __context) -> None:
        """Validate after all fields are set"""
        if self.type == QuestionType.MCQ:
            if self.options is None or len(self.options) == 0:
                raise ValueError("MCQ questions must have at least one option")


class SessionResponse(BaseModel):
    """Response for session operations"""
    status: str


class QuestionResponse(BaseModel):
    """Response for question creation"""
    question_id: str


class QuestionStopResponse(BaseModel):
    """Response for question stop"""
    status: str
    question_id: str


class DistributionResponse(BaseModel):
    """Response for distribution display"""
    question_id: str
    type: str
    counts: dict[str, int]
    total: int
    percentages: dict[str, float]
    options: list[str] | None = None


class QuestionResultsShareResponse(BaseModel):
    """Response for sharing results with students"""
    status: str
    question_id: str


@router.get("/c/{course}/admin", response_class=HTMLResponse)
async def admin_page(request: Request, course: str) -> Response:
    """
    Admin page - shows login if not authenticated, dashboard if authenticated
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check if admin is authenticated
    admin_cookie = request.cookies.get("admin_session")
    is_authenticated = verify_admin_cookie(admin_cookie, course, app.config.settings)

    if is_authenticated:
        # Check if session is currently live
        import redis
        redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
        redis_wrapper = RedisClient(redis_conn)
        session_is_live = redis_wrapper.is_session_live(course)
        redis_conn.close()

        # Show admin dashboard
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "session_is_live": session_is_live,
            },
        )
    else:
        # Show login page
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": None,
            },
        )


@router.post("/c/{course}/admin/login")
async def admin_login(
    request: Request,
    course: str,
    secret: str = Form(...),
) -> Response:
    """
    Admin login endpoint - verifies secret and sets cookie
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Verify secret
    if secret != course_config.secret:
        # Return to login page with error
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "error": "Invalid admin secret",
            },
            status_code=401,
        )

    # Create admin cookie
    cookie = create_admin_cookie(course, course_config.secret, app.config.settings.secret_key)

    # Redirect to admin dashboard (include root_path for subpath deployments)
    root_path = request.scope.get("root_path", "")
    response = RedirectResponse(
        url=f"{root_path}/c/{course}/admin",
        status_code=303,
    )
    response.set_cookie(
        key="admin_session",
        value=cookie,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
        max_age=86400,  # 24 hours
    )

    return response


# Session Management Routes

@router.post("/c/{course}/admin/session/start")
async def start_session(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> SessionResponse:
    """
    Start a session for the course
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Start session in Redis
    redis_client.start_session(course)

    # Publish SSE event
    redis_client.publish_event(course, EventType.SESSION_STARTED, {})

    return SessionResponse(status="started")


@router.post("/c/{course}/admin/session/stop")
async def stop_session(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> SessionResponse:
    """
    Stop a session for the course
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Stop session in Redis with 30 minute TTL
    redis_client.stop_session(course, ttl=1800)

    # Publish SSE event
    redis_client.publish_event(course, EventType.SESSION_STOPPED, {})

    return SessionResponse(status="stopped")


# Question Management Routes

@router.post("/c/{course}/admin/question")
async def create_question(
    request: Request,
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> QuestionResponse:
    """
    Create a new question (accepts both JSON and form data)
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Verify session is live
    if not redis_client.is_session_live(course):
        raise HTTPException(
            status_code=400,
            detail="Cannot create question when session is not active",
        )

    # Parse request body based on Content-Type
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # Parse as JSON
        try:
            body = await request.json()
            type_str = body.get("type")
            options = body.get("options")
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid JSON body: {str(e)}",
            )
    else:
        # Parse as form data
        try:
            form = await request.form()
            type_str = form.get("type")
            # Form data returns options as multiple values
            options = form.getlist("options") if "options" in form else None
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid form data: {str(e)}",
            )

    # Validate type field exists
    if type_str is None:
        raise HTTPException(
            status_code=422,
            detail="Field 'type' is required",
        )

    # Validate and parse question type
    try:
        qtype = QuestionType(type_str)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid question type: {type_str}",
        )

    # Validate MCQ has options
    if qtype == QuestionType.MCQ:
        if options is None or len(options) == 0:
            raise HTTPException(
                status_code=422,
                detail="MCQ questions must have at least one option",
            )

    # Create question in Redis
    question_id = redis_client.create_question(
        course=course,
        qtype=qtype,
        options=options,
    )

    # Publish SSE event
    event_data = {
        "question_id": question_id,
        "type": qtype.value,
    }
    if options:
        event_data["options"] = options

    redis_client.publish_event(course, EventType.QUESTION_STARTED, event_data)

    return QuestionResponse(question_id=question_id)


@router.post("/c/{course}/admin/question/{qid}/stop")
async def stop_question(
    course: str,
    qid: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> QuestionStopResponse:
    """
    Stop a question
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Stop question in Redis
    redis_client.stop_question(course, qid)

    # Publish SSE event
    redis_client.publish_event(
        course,
        EventType.QUESTION_STOPPED,
        {"question_id": qid},
    )

    return QuestionStopResponse(status="stopped", question_id=qid)


@router.post("/c/{course}/admin/question/{qid}/share-results")
async def share_results_with_students(
    course: str,
    qid: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> QuestionResultsShareResponse:
    """Share a question's distribution with students after it ends."""

    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    meta = redis_client.get_question_meta(course, qid)
    if meta is None:
        raise HTTPException(status_code=404, detail="Question not found")

    if meta.get("ended_at") is None:
        raise HTTPException(
            status_code=400,
            detail="Question must be stopped before sharing results",
        )

    # Build distribution to ensure data exists (and to reuse options/percentages)
    distribution = build_distribution(redis_client, course, qid)
    if distribution is None:
        raise HTTPException(status_code=404, detail="Question metadata not found")

    marked = redis_client.mark_results_shared(course, qid)
    if not marked:
        # Already shared by another admin
        return QuestionResultsShareResponse(status="already_shared", question_id=qid)

    redis_client.publish_event(
        course,
        EventType.RESULTS_PUBLISHED,
        {
            "question_id": qid,
            "distribution": distribution,
        },
    )

    return QuestionResultsShareResponse(status="shared", question_id=qid)


@router.get("/c/{course}/admin/distribution")
async def get_distribution(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> DistributionResponse:
    """
    Get the current question's answer distribution
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Get current question ID
    current_qid = redis_client.get_current_question(course)
    if current_qid is None:
        raise HTTPException(status_code=404, detail="No active question")

    distribution = build_distribution(redis_client, course, current_qid)
    if distribution is None:
        raise HTTPException(status_code=404, detail="Question metadata not found")

    return DistributionResponse(**distribution)


@router.get("/c/{course}/admin/export")
async def export_session_data(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> JSONResponse:
    """
    Export all session data as JSON
    DEPRECATED: Use /c/{course}/admin/archives instead
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Get all question IDs for this course
    question_ids = redis_client.get_all_question_ids(course)

    # Build export data
    export_data = []

    for qid in question_ids:
        # Get question metadata
        question_meta = redis_client.get_question_meta(course, qid)
        if question_meta is None:
            continue

        # Get all responses for this question
        all_responses = redis_client.get_all_responses(course, qid)

        # Format responses
        formatted_responses = {}
        for pid, response_data in all_responses.items():
            formatted_responses[pid] = {
                "timestamp": response_data["ts"],
                "response": response_data["resp"],
            }

        # Build question export object
        question_export = {
            "question_id": qid,
            "type": question_meta["type"],
            "responses": formatted_responses,
        }

        # Add options for MCQ
        if "options" in question_meta and question_meta["options"] is not None:
            question_export["options"] = question_meta["options"]

        # Add timestamps
        if "started_at" in question_meta:
            question_export["started_at"] = question_meta["started_at"]
        if "ended_at" in question_meta:
            question_export["ended_at"] = question_meta["ended_at"]

        export_data.append(question_export)

    return JSONResponse(content=export_data)


# Archive Routes


@router.get("/c/{course}/admin/archives", response_class=HTMLResponse)
async def archives_page(
    request: Request,
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> Response:
    """
    Archives page - shows list of archived sessions
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Get archived sessions
    archives = redis_client.get_archived_sessions(course)

    # Check if a session is currently live
    session_is_live = redis_client.is_session_live(course)

    return templates.TemplateResponse(
        request=request,
        name="archives.html",
        context={
            "course_name": course_config.name,
            "course_slug": course,
            "archives": archives,
            "session_is_live": session_is_live,
        },
    )


@router.get("/c/{course}/admin/archives/{session_id}")
async def download_archive(
    course: str,
    session_id: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> JSONResponse:
    """
    Download an archived session as JSON
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Get archived session
    archive_data = redis_client.get_archived_session(course, session_id)

    if archive_data is None:
        raise HTTPException(status_code=404, detail="Archived session not found")

    return JSONResponse(content=archive_data)


# Student Q&A Routes


@router.get("/c/{course}/admin/questions")
async def get_student_questions(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> list[dict]:
    """
    Get all student questions for a course
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Get all questions
    questions = redis_client.get_all_questions(course)

    return questions


@router.delete("/c/{course}/admin/questions/{question_id}")
async def dismiss_question(
    course: str,
    question_id: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> dict:
    """
    Dismiss (delete) a student question
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Delete the question
    deleted = redis_client.delete_question(course, question_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Question not found")

    return {"status": "dismissed", "question_id": question_id}
