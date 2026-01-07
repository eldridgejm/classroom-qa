"""
Authentication and authorization helpers
"""

import hmac
import re
import secrets

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

from app.config import Settings

# PID format validation (UCSD: A########)
PID_PATTERN = re.compile(r"^A\d{8}$")


def validate_pid_format(pid: str) -> bool:
    """
    Validate PID format (A followed by 8 digits)

    Args:
        pid: Student PID to validate

    Returns:
        True if valid format, False otherwise
    """
    return bool(PID_PATTERN.match(pid))


# PID Cookie Management


def create_pid_cookie(
    pid: str,
    secret_key: str,
) -> str:
    """
    Create a signed cookie for a student PID

    Args:
        pid: Student PID
        secret_key: Secret key for signing

    Returns:
        Signed cookie string
    """
    signer = TimestampSigner(secret_key)
    return signer.sign(pid).decode()


def verify_pid_cookie(
    cookie: str | None,
    secret_key: str,
    max_age: int | None = None,
) -> str | None:
    """
    Verify a PID cookie and return the PID

    Args:
        cookie: Signed cookie string
        secret_key: Secret key for verification
        max_age: Optional max age in seconds (None = no limit)

    Returns:
        PID if valid, None if invalid or expired
    """
    if not cookie:
        return None

    try:
        signer = TimestampSigner(secret_key)
        # If max_age is provided, check expiration
        if max_age is not None:
            pid = signer.unsign(cookie, max_age=max_age).decode()
        else:
            pid = signer.unsign(cookie).decode()
        return pid
    except (BadSignature, SignatureExpired):
        return None
    except Exception:
        # Catch any other exceptions (e.g., decoding errors)
        return None


# Admin Cookie Management


def create_admin_cookie(
    course: str,
    course_secret: str,
    secret_key: str,
) -> str:
    """
    Create a signed cookie for admin authentication

    Args:
        course: Course slug
        course_secret: Course's admin secret
        secret_key: Secret key for signing

    Returns:
        Signed cookie string
    """
    signer = TimestampSigner(secret_key)
    # Combine course slug and secret for verification
    data = f"{course}:{course_secret}"
    return signer.sign(data).decode()


def verify_admin_cookie(
    cookie: str | None,
    course: str,
    settings: Settings,
    max_age: int | None = None,
) -> bool:
    """
    Verify an admin cookie for a specific course

    Args:
        cookie: Signed cookie string
        course: Course slug to verify against
        settings: Application settings (to get course config)
        max_age: Optional max age in seconds (None = no limit)

    Returns:
        True if valid, False if invalid or expired
    """
    if not cookie:
        return False

    try:
        signer = TimestampSigner(settings.secret_key)

        # Unsign the cookie
        if max_age is not None:
            data = signer.unsign(cookie, max_age=max_age).decode()
        else:
            data = signer.unsign(cookie).decode()

        # Parse the data
        cookie_course, cookie_secret = data.split(":", 1)

        # Verify course matches
        if cookie_course != course:
            return False

        # Get the actual course configuration
        course_config = settings.get_course(course)
        if course_config is None:
            return False

        # Verify the secret matches
        return hmac.compare_digest(cookie_secret, course_config.secret)

    except (BadSignature, SignatureExpired):
        return False
    except Exception:
        # Catch any other exceptions (e.g., parsing errors)
        return False


# CSRF Token Management


def create_csrf_token() -> str:
    """
    Generate a CSRF token

    Returns:
        Random CSRF token (hex string)
    """
    return secrets.token_hex(32)


def verify_csrf_token(token: str, expected: str) -> bool:
    """
    Verify a CSRF token against expected value

    Args:
        token: Token to verify
        expected: Expected token value

    Returns:
        True if tokens match, False otherwise
    """
    if not token or not expected:
        return False

    # Use constant-time comparison to prevent timing attacks
    return hmac.compare_digest(token, expected)


# FastAPI Dependencies


def require_pid(
    cookie: str | None,
    secret_key: str,
    max_age: int | None = None,
) -> str:
    """
    FastAPI dependency to require a valid PID cookie

    Args:
        cookie: PID cookie from request
        secret_key: Secret key for verification
        max_age: Optional max age in seconds (None = no limit)

    Returns:
        Verified PID

    Raises:
        HTTPException: If cookie is invalid or missing
    """
    pid = verify_pid_cookie(cookie, secret_key, max_age=max_age)

    if pid is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing PID cookie",
        )

    return pid


def require_admin(
    cookie: str | None,
    course: str,
    settings: Settings,
    max_age: int | None = None,
) -> None:
    """
    FastAPI dependency to require valid admin credentials

    Args:
        cookie: Admin cookie from request
        course: Course slug
        settings: Application settings
        max_age: Optional max age in seconds (None = no limit)

    Raises:
        HTTPException: If credentials are invalid
    """
    is_valid = verify_admin_cookie(cookie, course, settings, max_age=max_age)

    if not is_valid:
        raise HTTPException(
            status_code=403,
            detail="Invalid admin credentials",
        )
