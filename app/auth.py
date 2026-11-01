"""
Authentication and authorization helpers
"""

import hashlib
import hmac
import re
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner, URLSafeTimedSerializer

from app.config import Settings

# TSN format validation (UCSD Triton Student Number: 9 digits)
TSN_PATTERN = re.compile(r"[0-9]{9}")


def validate_tsn_format(tsn: str) -> bool:
    """
    Validate TSN format (exactly 9 digits)

    Args:
        tsn: Student TSN to validate

    Returns:
        True if valid format, False otherwise
    """
    return bool(TSN_PATTERN.fullmatch(tsn))


# TSN Cookie Management


def create_tsn_cookie(
    tsn: str,
    secret_key: str,
) -> str:
    """
    Create a signed cookie for a student TSN

    Args:
        tsn: Student TSN
        secret_key: Secret key for signing

    Returns:
        Signed cookie string
    """
    signer = TimestampSigner(secret_key)
    return signer.sign(tsn).decode()


def verify_tsn_cookie(
    cookie: str | None,
    secret_key: str,
    max_age: int | None = None,
) -> str | None:
    """
    Verify a TSN cookie and return the TSN

    Args:
        cookie: Signed cookie string
        secret_key: Secret key for verification
        max_age: Optional max age in seconds (None = no limit)

    Returns:
        TSN if valid, None if invalid, expired, or not a well-formed TSN
        (e.g., a legacy PID cookie issued before the switch to TSNs)
    """
    if not cookie:
        return None

    try:
        signer = TimestampSigner(secret_key)
        # If max_age is provided, check expiration
        if max_age is not None:
            tsn = signer.unsign(cookie, max_age=max_age).decode()
        else:
            tsn = signer.unsign(cookie).decode()
        return tsn if validate_tsn_format(tsn) else None
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


# "Remember this device" for admin login
#
# A remembered device holds a long-lived cookie (one per course) carrying a
# signed, timestamped token. The token names the course, the course's current
# remember version (a counter in Redis that "revoke all" bumps), and a
# fingerprint of the course's admin secret, so changing the admin secret also
# revokes every remembered device. Unlike the admin_session cookie, the token
# does not contain the admin secret itself.

REMEMBER_COOKIE_PREFIX = "admin_remember_"
_REMEMBER_SALT = "admin-remember-device"


def remember_cookie_name(course: str) -> str:
    """Name of the course's remember-device cookie (one per course, so several can coexist)"""
    # Cookie names are HTTP tokens; slugs are URL path segments and normally
    # fit already. Mapping anything else to "_" can't confuse courses, since
    # the token itself names its course.
    return REMEMBER_COOKIE_PREFIX + re.sub(r"[^A-Za-z0-9_-]", "_", course)


def _course_secret_fingerprint(course_secret: str, secret_key: str) -> str:
    return hmac.new(
        secret_key.encode(), f"remember:{course_secret}".encode(), hashlib.sha256
    ).hexdigest()[:32]


def create_remember_token(course: str, course_secret: str, version: int, secret_key: str) -> str:
    """
    Create a signed, timestamped remember-device token for a course

    Args:
        course: Course slug
        course_secret: Course's admin secret (only a fingerprint goes in the token)
        version: The course's current remember version (from Redis)
        secret_key: The app's secret key, used to sign
    """
    serializer = URLSafeTimedSerializer(secret_key, salt=_REMEMBER_SALT)
    return serializer.dumps(
        {
            "c": course,
            "v": version,
            "k": _course_secret_fingerprint(course_secret, secret_key),
        }
    )


def verify_remember_token(
    token: str | None,
    course: str,
    current_version: int,
    settings: Settings,
) -> bool:
    """
    Check a remember-device token for a course

    Valid only if the signature is good, it is younger than
    settings.admin_remember_days, it names this course, its version is the
    course's current one, and the course's admin secret hasn't changed.
    """
    if not token:
        return False

    course_config = settings.get_course(course)
    if course_config is None:
        return False

    serializer = URLSafeTimedSerializer(settings.secret_key, salt=_REMEMBER_SALT)
    try:
        data = serializer.loads(token, max_age=max(0, settings.admin_remember_days) * 86400)
    except (BadSignature, SignatureExpired):
        return False
    except Exception:
        return False

    if not isinstance(data, dict):
        return False

    expected_fp = _course_secret_fingerprint(course_config.secret, settings.secret_key)
    return (
        data.get("c") == course
        and type(data.get("v")) is int
        and data["v"] == current_version
        and isinstance(data.get("k"), str)
        and hmac.compare_digest(data["k"], expected_fp)
    )


def request_is_https(request: Request) -> bool:
    """
    Whether the browser reached this page over HTTPS (for the cookie Secure flag)

    The request's own scheme comes from uvicorn, which takes it from
    X-Forwarded-Proto when the proxy is trusted. That is right when HTTPS ends
    at the proxy in front of the app, but not when HTTPS ends at a hop in
    front of that proxy, which then forwards plain HTTP and reports "http".
    So also look at what the browser itself says: browsers send Origin (or
    failing that Referer) on form POSTs, giving the scheme of the page the
    form was on, and that is exactly the connection the cookie travels over.
    These headers are client-controlled, but a false value only changes the
    flags on the client's own cookie.
    """
    if request.url.scheme == "https":
        return True
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and value != "null":
            return urlsplit(value).scheme == "https"
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


def require_tsn(
    cookie: str | None,
    secret_key: str,
    max_age: int | None = None,
) -> str:
    """
    FastAPI dependency to require a valid TSN cookie

    Args:
        cookie: TSN cookie from request
        secret_key: Secret key for verification
        max_age: Optional max age in seconds (None = no limit)

    Returns:
        Verified TSN

    Raises:
        HTTPException: If cookie is invalid or missing
    """
    tsn = verify_tsn_cookie(cookie, secret_key, max_age=max_age)

    if tsn is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing TSN cookie",
        )

    return tsn


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
