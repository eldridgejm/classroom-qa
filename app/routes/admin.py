"""
Admin routes for course management
"""

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.config
from app.auth import (
    create_admin_cookie,
    create_remember_token,
    remember_cookie_name,
    request_is_https,
    verify_admin_cookie,
    verify_remember_token,
)
from app.models import EventType, QuestionType
from app.redis_client import RedisClient
from app.services import sessions
from app.services.distribution import build_distribution
from app.sheet_files import SheetFile, list_sheet_uploads, sniff_sheet_type, write_sheet_upload

logger = logging.getLogger(__name__)

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


def is_remembered_device(request: Request, course: str) -> bool:
    """Whether the request carries a valid, unrevoked remember-device cookie for the course"""
    token = request.cookies.get(remember_cookie_name(course))
    if not token:
        return False
    redis_client = get_redis_client()
    try:
        version = redis_client.get_admin_remember_version(course)
    finally:
        redis_client.redis.close()
    return verify_remember_token(token, course, version, app.config.settings)


def is_admin_authenticated(request: Request, course: str) -> bool:
    """
    Whether the request is logged in as the course's admin: either a valid
    admin_session cookie (password login) or a remembered device
    """
    if verify_admin_cookie(request.cookies.get("admin_session"), course, app.config.settings):
        return True
    return is_remembered_device(request, course)


# Dependency to verify admin authentication
def verify_admin_auth(request: Request, course: str) -> None:
    """Verify admin authentication"""
    if not is_admin_authenticated(request, course):
        raise HTTPException(status_code=403, detail="Invalid admin credentials")


# Admin pages the login form may return to (besides the dashboard), by name.
# A fixed list, so the login can't be used as an open redirect.
LOGIN_NEXT_PAGES = {"sheets": "admin/sheets"}


def render_login(
    request: Request,
    course: str,
    course_name: str,
    error: str | None = None,
    next_page: str | None = None,
    status_code: int = 200,
) -> Response:
    """Render the admin login page"""
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={
            "course_name": course_name,
            "course_slug": course,
            "error": error,
            "next_page": next_page if next_page in LOGIN_NEXT_PAGES else None,
        },
        status_code=status_code,
    )



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


class CurrentQuestion(BaseModel):
    """The question currently accepting answers"""
    id: str
    type: str
    options: list[str] | None = None


class AdminStateResponse(BaseModel):
    """Current session and question state, for re-syncing the dashboard"""
    session_live: bool
    current_question: CurrentQuestion | None = None


def get_admin_state(redis_client: RedisClient, course: str) -> AdminStateResponse:
    """Read the live session and current question from Redis"""
    session_live = redis_client.is_session_live(course)

    current_question = None
    if session_live:
        meta = redis_client.get_active_question(course)
        if meta is not None:
            current_question = CurrentQuestion(
                id=meta["id"], type=meta["type"], options=meta.get("options")
            )

    return AdminStateResponse(session_live=session_live, current_question=current_question)


class QuestionResultsShareResponse(BaseModel):
    """Response for sharing results with students"""
    status: str
    question_id: str


@router.get("/{course}/admin", response_class=HTMLResponse)
async def admin_page(request: Request, course: str) -> Response:
    """
    Admin page - shows login if not authenticated, dashboard if authenticated
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check if admin is authenticated
    is_authenticated = is_admin_authenticated(request, course)

    if is_authenticated:
        # Check if session is currently live
        import redis
        redis_conn = redis.from_url(app.config.settings.redis_url, decode_responses=True)
        redis_wrapper = RedisClient(redis_conn)
        state = get_admin_state(redis_wrapper, course)
        redis_conn.close()

        # Show admin dashboard
        return templates.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "course_name": course_config.name,
                "course_slug": course,
                "session_is_live": state.session_live,
                "current_question": (
                    state.current_question.model_dump() if state.current_question else None
                ),
                "sheets_enabled": bool(app.config.settings.sheets_dir),
                "device_remembered": is_remembered_device(request, course),
                "remember_days": app.config.settings.admin_remember_days,
            },
        )
    else:
        # Show login page
        return render_login(request, course, course_config.name)


@router.post("/{course}/admin/login")
async def admin_login(
    request: Request,
    course: str,
    secret: str = Form(...),
    remember: str | None = Form(None),
    next_page: str | None = Form(None, alias="next"),
) -> Response:
    """
    Admin login endpoint - verifies secret and sets cookie

    With "remember" checked, also sets a long-lived remember-device cookie.
    """
    # Check if course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    # Verify secret
    if secret != course_config.secret:
        # Return to login page with error
        return render_login(
            request,
            course,
            course_config.name,
            error="Invalid admin secret",
            next_page=next_page,
            status_code=401,
        )

    # Create admin cookie
    cookie = create_admin_cookie(course, course_config.secret, app.config.settings.secret_key)

    # Redirect to admin dashboard, or the admin page the login came from
    # (include root_path for subpath deployments)
    root_path = request.scope.get("root_path", "")
    target = LOGIN_NEXT_PAGES.get(next_page or "", "admin")
    response = RedirectResponse(
        url=f"{root_path}/{course}/{target}",
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

    if remember:
        settings = app.config.settings
        redis_client = get_redis_client()
        try:
            version = redis_client.get_admin_remember_version(course)
        finally:
            redis_client.redis.close()
        response.set_cookie(
            key=remember_cookie_name(course),
            value=create_remember_token(
                course, course_config.secret, version, settings.secret_key
            ),
            max_age=max(0, settings.admin_remember_days) * 86400,
            path=f"{root_path}/",
            httponly=True,
            secure=request_is_https(request),
            samesite="lax",
        )

    return response


@router.post("/{course}/admin/remembered-devices/revoke")
async def revoke_remembered_devices(
    request: Request,
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> Response:
    """
    Log out every remembered device for the course (including this one) by
    bumping the course's remember version. Password logins are unaffected.
    """
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    redis_client.revoke_admin_remembered_devices(course)

    root_path = request.scope.get("root_path", "")
    response = RedirectResponse(url=f"{root_path}/{course}/admin", status_code=303)
    response.delete_cookie(
        key=remember_cookie_name(course),
        path=f"{root_path}/",
        httponly=True,
        secure=request_is_https(request),
        samesite="lax",
    )
    return response


# Session Management Routes

@router.post("/{course}/admin/session/start")
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


@router.post("/{course}/admin/session/stop")
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

    # Archive (to Redis for the configured TTL, and to disk if configured),
    # clear the session, and publish SESSION_STOPPED
    try:
        sessions.stop_session(redis_client, course, app.config.settings)
    except sessions.StopInProgressError:
        raise HTTPException(
            status_code=409, detail="Session is already being stopped"
        ) from None

    return SessionResponse(status="stopped")


@router.get("/{course}/admin/state")
async def get_state(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> AdminStateResponse:
    """
    Get the current session and question state
    """
    # Verify course exists
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    return get_admin_state(redis_client, course)


# Question Management Routes

@router.post("/{course}/admin/question")
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


@router.post("/{course}/admin/question/{qid}/stop")
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


@router.post("/{course}/admin/question/{qid}/share-results")
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


@router.get("/{course}/admin/distribution")
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


@router.get("/{course}/admin/export")
async def export_session_data(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
    redis_client: Annotated[RedisClient, Depends(get_redis_client)],
) -> JSONResponse:
    """
    Export all session data as JSON
    DEPRECATED: Use /{course}/admin/archives instead
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
        for tsn, response_data in all_responses.items():
            formatted_responses[tsn] = {
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


@router.get("/{course}/admin/archives", response_class=HTMLResponse)
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


@router.get("/{course}/admin/archives/{session_id}")
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


@router.get("/{course}/admin/questions")
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


@router.delete("/{course}/admin/questions/{question_id}")
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


# Attendance Sheet Uploads


def _sheets_context(course: str) -> dict[str, Any]:
    settings = app.config.settings
    return {
        "max_files": settings.sheets_max_files,
        "max_file_bytes": settings.sheets_max_file_bytes,
        "max_upload_bytes": settings.sheets_max_upload_bytes,
    }


def _recent_uploads(course: str) -> list[dict[str, Any]]:
    sheets_dir = app.config.settings.sheets_dir
    if not sheets_dir:
        return []
    return [
        {
            "id": m.get("id"),
            "uploaded_at": m.get("uploaded_at"),
            "lecture": m.get("lecture"),
            "images": sum(1 for f in m["files"] if str(f.get("type", "")).startswith("image/")),
            "pdfs": sum(1 for f in m["files"] if f.get("type") == "application/pdf"),
        }
        for m in list_sheet_uploads(sheets_dir, course)
    ]


@router.get("/{course}/admin/sheets", response_class=HTMLResponse)
async def sheets_page(request: Request, course: str) -> Response:
    """
    Page for photographing paper attendance sheets and uploading them
    (shows the login form, returning here, if not authenticated)
    """
    course_config = app.config.settings.get_course(course)
    if course_config is None:
        raise HTTPException(status_code=404, detail="Course not found")

    if not is_admin_authenticated(request, course):
        return render_login(request, course, course_config.name, next_page="sheets")

    return templates.TemplateResponse(
        request=request,
        name="sheets.html",
        context={
            "course_name": course_config.name,
            "course_slug": course,
            "enabled": bool(app.config.settings.sheets_dir),
            "uploads": _recent_uploads(course),
            "limits": _sheets_context(course),
        },
    )


@router.get("/{course}/admin/sheets/recent")
async def recent_sheet_uploads(
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
) -> list[dict[str, Any]]:
    """The course's most recent sheet uploads (newest first)"""
    if app.config.settings.get_course(course) is None:
        raise HTTPException(status_code=404, detail="Course not found")
    return _recent_uploads(course)


def _mib(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


@router.post("/{course}/admin/sheets")
async def upload_sheets(
    request: Request,
    course: str,
    _: Annotated[None, Depends(verify_admin_auth)],
) -> dict[str, Any]:
    """
    Upload photos or PDFs of attendance sheets (multipart form: "files", one
    per page/PDF in order, and optional "lecture", 0-99; blank = next lecture)
    """
    settings = app.config.settings
    if settings.get_course(course) is None:
        raise HTTPException(status_code=404, detail="Course not found")
    if not settings.sheets_dir:
        raise HTTPException(status_code=503, detail="Sheet uploads are not enabled")

    max_files = settings.sheets_max_files
    max_file = settings.sheets_max_file_bytes
    max_total = settings.sheets_max_upload_bytes

    # Reject an obviously oversized body before parsing it (allowing for
    # multipart overhead); the total is checked again after parsing.
    try:
        content_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        content_length = 0
    if content_length > max_total + 64 * 1024 + 2048 * (max_files + 2):
        raise HTTPException(
            status_code=413, detail=f"Upload too large (at most {_mib(max_total)} in total)"
        )

    try:
        form = await request.form(max_files=max_files, max_fields=10)
    except StarletteHTTPException as exc:
        if "Too many files" in str(exc.detail):
            raise HTTPException(
                status_code=422, detail=f"Too many files (at most {max_files} per upload)"
            ) from None
        raise

    try:
        lecture_raw = form.get("lecture")
        lecture_str = lecture_raw.strip() if isinstance(lecture_raw, str) else ""
        lecture: int | None = None
        if lecture_str:
            if not (lecture_str.isascii() and lecture_str.isdigit() and len(lecture_str) <= 2):
                raise HTTPException(
                    status_code=422,
                    detail="Lecture must be a number from 0 to 99 (or blank for the next lecture)",
                )
            lecture = int(lecture_str)

        uploads = [f for f in form.getlist("files") if isinstance(f, StarletteUploadFile)]
        if not uploads:
            raise HTTPException(status_code=422, detail="No files were uploaded")
        if len(uploads) > max_files:
            raise HTTPException(
                status_code=422, detail=f"Too many files (at most {max_files} per upload)"
            )

        files: list[SheetFile] = []
        total = 0
        for i, upload in enumerate(uploads, start=1):
            data = await upload.read(max_file + 1)
            if len(data) > max_file:
                raise HTTPException(
                    status_code=413, detail=f"File {i} is too large (at most {_mib(max_file)})"
                )
            if not data:
                raise HTTPException(status_code=422, detail=f"File {i} is empty")
            total += len(data)
            if total > max_total:
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload too large (at most {_mib(max_total)} in total)",
                )
            sheet_type = sniff_sheet_type(data[:64])
            if sheet_type is None:
                raise HTTPException(
                    status_code=415,
                    detail=f"File {i} is not a JPEG, PNG, HEIC, or PDF",
                )
            files.append(SheetFile(data=data, type=sheet_type, original_name=upload.filename))
    finally:
        await form.close()

    try:
        manifest = await asyncio.to_thread(
            write_sheet_upload, settings.sheets_dir, course, files, lecture
        )
    except Exception:
        logger.exception("Failed to store sheet upload for %s", course)
        raise HTTPException(
            status_code=500, detail="The server could not save the upload; nothing was saved"
        ) from None

    logger.info(
        "Stored sheet upload %s for %s (%d files, lecture %s)",
        manifest["id"],
        course,
        len(files),
        lecture,
    )
    return {
        "id": manifest["id"],
        "uploaded_at": manifest["uploaded_at"],
        "lecture": lecture,
        "files": len(files),
        "images": sum(1 for f in files if f.type.is_image),
        "pdfs": sum(1 for f in files if not f.type.is_image),
    }
