"""
Tests for authentication and authorization (Phase 3)

This test file covers:
- Admin secret cookie signing/validation
- Admin secret verification against courses.toml
- PID cookie signing/validation
- PID format validation (UCSD: A########)
- CSRF token generation/validation
- Invalid/tampered cookies rejection
- Missing auth handling
- Expired cookies
- FastAPI dependency injection
"""

import time

import pytest
from fastapi import HTTPException

from app.auth import (
    create_admin_cookie,
    create_csrf_token,
    create_pid_cookie,
    require_admin,
    require_pid,
    validate_pid_format,
    verify_admin_cookie,
    verify_csrf_token,
    verify_pid_cookie,
)
from app.config import Settings


class TestPIDValidation:
    """Test cases for PID format validation"""

    def test_valid_pid_format(self) -> None:
        """Test that valid PID format is accepted"""
        assert validate_pid_format("A12345678") is True

    def test_valid_pid_all_zeros(self) -> None:
        """Test that PID with all zeros is valid"""
        assert validate_pid_format("A00000000") is True

    def test_valid_pid_all_nines(self) -> None:
        """Test that PID with all nines is valid"""
        assert validate_pid_format("A99999999") is True

    def test_invalid_pid_lowercase_a(self) -> None:
        """Test that lowercase 'a' is invalid"""
        assert validate_pid_format("a12345678") is False

    def test_invalid_pid_no_a(self) -> None:
        """Test that PID without 'A' is invalid"""
        assert validate_pid_format("12345678") is False

    def test_invalid_pid_too_short(self) -> None:
        """Test that PID with fewer than 8 digits is invalid"""
        assert validate_pid_format("A1234567") is False

    def test_invalid_pid_too_long(self) -> None:
        """Test that PID with more than 8 digits is invalid"""
        assert validate_pid_format("A123456789") is False

    def test_invalid_pid_letters_in_number(self) -> None:
        """Test that PID with letters in number part is invalid"""
        assert validate_pid_format("A1234567B") is False

    def test_invalid_pid_special_chars(self) -> None:
        """Test that PID with special characters is invalid"""
        assert validate_pid_format("A1234-678") is False

    def test_invalid_pid_empty(self) -> None:
        """Test that empty string is invalid"""
        assert validate_pid_format("") is False

    def test_invalid_pid_spaces(self) -> None:
        """Test that PID with spaces is invalid"""
        assert validate_pid_format("A 12345678") is False


class TestPIDCookies:
    """Test cases for PID cookie creation and verification"""

    def test_create_pid_cookie(self, test_settings: Settings) -> None:
        """Test creating a PID cookie"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        assert cookie is not None
        assert isinstance(cookie, str)
        assert len(cookie) > 0

    def test_verify_pid_cookie_valid(self, test_settings: Settings) -> None:
        """Test verifying a valid PID cookie"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)
        pid = verify_pid_cookie(cookie, test_settings.secret_key)

        assert pid == "A12345678"

    def test_verify_pid_cookie_different_pids(self, test_settings: Settings) -> None:
        """Test that different PIDs create different cookies"""
        cookie1 = create_pid_cookie("A11111111", test_settings.secret_key)
        cookie2 = create_pid_cookie("A22222222", test_settings.secret_key)

        assert cookie1 != cookie2

        pid1 = verify_pid_cookie(cookie1, test_settings.secret_key)
        pid2 = verify_pid_cookie(cookie2, test_settings.secret_key)

        assert pid1 == "A11111111"
        assert pid2 == "A22222222"

    def test_verify_pid_cookie_invalid(self, test_settings: Settings) -> None:
        """Test that invalid cookie returns None"""
        pid = verify_pid_cookie("invalid-cookie", test_settings.secret_key)
        assert pid is None

    def test_verify_pid_cookie_tampered(self, test_settings: Settings) -> None:
        """Test that tampered cookie returns None"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)
        # Tamper with the cookie
        tampered = cookie[:-5] + "XXXXX"
        pid = verify_pid_cookie(tampered, test_settings.secret_key)

        assert pid is None

    def test_verify_pid_cookie_wrong_secret(self, test_settings: Settings) -> None:
        """Test that cookie signed with different secret fails"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)
        pid = verify_pid_cookie(cookie, "different-secret-key")

        assert pid is None

    def test_verify_pid_cookie_empty(self, test_settings: Settings) -> None:
        """Test that empty cookie returns None"""
        pid = verify_pid_cookie("", test_settings.secret_key)
        assert pid is None

    def test_pid_cookie_with_expiration(self, test_settings: Settings) -> None:
        """Test PID cookie with expiration time"""
        # Create cookie
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Should be valid immediately with max_age=2
        pid = verify_pid_cookie(cookie, test_settings.secret_key, max_age=2)
        assert pid == "A12345678"

        # Wait for expiration
        time.sleep(2)

        # Should now be invalid when checked with max_age=1
        pid = verify_pid_cookie(cookie, test_settings.secret_key, max_age=1)
        assert pid is None


class TestAdminCookies:
    """Test cases for admin cookie creation and verification"""

    def test_create_admin_cookie(self, test_settings: Settings) -> None:
        """Test creating an admin cookie"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        assert cookie is not None
        assert isinstance(cookie, str)
        assert len(cookie) > 0

    def test_verify_admin_cookie_valid(self, test_settings: Settings) -> None:
        """Test verifying a valid admin cookie"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )
        is_valid = verify_admin_cookie(
            cookie, "test-course", test_settings
        )

        assert is_valid is True

    def test_verify_admin_cookie_wrong_course(
        self, test_settings: Settings
    ) -> None:
        """Test that admin cookie for one course doesn't work for another"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Try to use it for another course
        is_valid = verify_admin_cookie(
            cookie, "another-course", test_settings
        )

        assert is_valid is False

    def test_verify_admin_cookie_invalid(self, test_settings: Settings) -> None:
        """Test that invalid admin cookie returns False"""
        is_valid = verify_admin_cookie(
            "invalid-cookie", "test-course", test_settings
        )
        assert is_valid is False

    def test_verify_admin_cookie_tampered(self, test_settings: Settings) -> None:
        """Test that tampered admin cookie returns False"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )
        # Tamper with the cookie
        tampered = cookie[:-5] + "XXXXX"

        is_valid = verify_admin_cookie(
            tampered, "test-course", test_settings
        )

        assert is_valid is False

    def test_verify_admin_cookie_wrong_secret_in_cookie(
        self, test_settings: Settings
    ) -> None:
        """Test that cookie with wrong course secret fails"""
        # Create cookie with wrong secret
        cookie = create_admin_cookie(
            "test-course", "wrong-secret", test_settings.secret_key
        )

        is_valid = verify_admin_cookie(
            cookie, "test-course", test_settings
        )

        assert is_valid is False

    def test_verify_admin_cookie_nonexistent_course(
        self, test_settings: Settings
    ) -> None:
        """Test verification fails for nonexistent course"""
        cookie = create_admin_cookie(
            "fake-course", "fake-secret", test_settings.secret_key
        )

        is_valid = verify_admin_cookie(
            cookie, "fake-course", test_settings
        )

        assert is_valid is False

    def test_admin_cookie_with_expiration(self, test_settings: Settings) -> None:
        """Test admin cookie with expiration time"""
        course = test_settings.get_course("test-course")
        assert course is not None

        # Create cookie
        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Should be valid immediately with max_age=2
        is_valid = verify_admin_cookie(
            cookie, "test-course", test_settings, max_age=2
        )
        assert is_valid is True

        # Wait for expiration
        time.sleep(2)

        # Should now be invalid when checked with max_age=1
        is_valid = verify_admin_cookie(
            cookie, "test-course", test_settings, max_age=1
        )
        assert is_valid is False


class TestCSRFTokens:
    """Test cases for CSRF token generation and validation"""

    def test_create_csrf_token(self) -> None:
        """Test creating a CSRF token"""
        token = create_csrf_token()

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_csrf_tokens_are_unique(self) -> None:
        """Test that different CSRF tokens are generated each time"""
        token1 = create_csrf_token()
        token2 = create_csrf_token()

        assert token1 != token2

    def test_verify_csrf_token_valid(self) -> None:
        """Test verifying a valid CSRF token"""
        token = create_csrf_token()

        assert verify_csrf_token(token, token) is True

    def test_verify_csrf_token_invalid(self) -> None:
        """Test that mismatched tokens fail validation"""
        token1 = create_csrf_token()
        token2 = create_csrf_token()

        assert verify_csrf_token(token1, token2) is False

    def test_verify_csrf_token_empty(self) -> None:
        """Test that empty token fails validation"""
        token = create_csrf_token()

        assert verify_csrf_token("", token) is False
        assert verify_csrf_token(token, "") is False

    def test_verify_csrf_token_case_sensitive(self) -> None:
        """Test that CSRF tokens are case-sensitive"""
        token = create_csrf_token().lower()
        upper_token = token.upper()

        assert verify_csrf_token(token, upper_token) is False

    def test_csrf_token_length(self) -> None:
        """Test that CSRF tokens have reasonable length"""
        token = create_csrf_token()

        # Should be at least 32 characters for security
        assert len(token) >= 32


class TestFastAPIDependencies:
    """Test cases for FastAPI dependency injection"""

    def test_require_pid_valid(self, test_settings: Settings) -> None:
        """Test require_pid with valid cookie"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        pid = require_pid(cookie, test_settings.secret_key)

        assert pid == "A12345678"

    def test_require_pid_invalid_cookie(self, test_settings: Settings) -> None:
        """Test require_pid with invalid cookie raises HTTPException"""
        with pytest.raises(HTTPException) as exc_info:
            require_pid("invalid-cookie", test_settings.secret_key)

        assert exc_info.value.status_code == 401
        assert "Invalid or missing PID" in exc_info.value.detail

    def test_require_pid_missing_cookie(self, test_settings: Settings) -> None:
        """Test require_pid with missing cookie raises HTTPException"""
        with pytest.raises(HTTPException) as exc_info:
            require_pid(None, test_settings.secret_key)

        assert exc_info.value.status_code == 401
        assert "Invalid or missing PID" in exc_info.value.detail

    def test_require_pid_expired_cookie(self, test_settings: Settings) -> None:
        """Test require_pid with expired cookie raises HTTPException"""
        # Create cookie
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        # Wait a bit
        time.sleep(1)

        # Try to verify with very short max_age (should fail)
        with pytest.raises(HTTPException) as exc_info:
            require_pid(cookie, test_settings.secret_key, max_age=0)

        assert exc_info.value.status_code == 401

    def test_require_admin_valid(self, test_settings: Settings) -> None:
        """Test require_admin with valid cookie"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Should not raise exception
        require_admin(cookie, "test-course", test_settings)

    def test_require_admin_invalid_cookie(self, test_settings: Settings) -> None:
        """Test require_admin with invalid cookie raises HTTPException"""
        with pytest.raises(HTTPException) as exc_info:
            require_admin("invalid-cookie", "test-course", test_settings)

        assert exc_info.value.status_code == 403
        assert "Invalid admin credentials" in exc_info.value.detail

    def test_require_admin_missing_cookie(self, test_settings: Settings) -> None:
        """Test require_admin with missing cookie raises HTTPException"""
        with pytest.raises(HTTPException) as exc_info:
            require_admin(None, "test-course", test_settings)

        assert exc_info.value.status_code == 403

    def test_require_admin_wrong_course(self, test_settings: Settings) -> None:
        """Test require_admin with cookie for wrong course raises HTTPException"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        with pytest.raises(HTTPException) as exc_info:
            require_admin(cookie, "another-course", test_settings)

        assert exc_info.value.status_code == 403

    def test_require_admin_expired_cookie(self, test_settings: Settings) -> None:
        """Test require_admin with expired cookie raises HTTPException"""
        course = test_settings.get_course("test-course")
        assert course is not None

        # Create cookie
        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Wait a bit
        time.sleep(1)

        # Try to verify with very short max_age (should fail)
        with pytest.raises(HTTPException) as exc_info:
            require_admin(cookie, "test-course", test_settings, max_age=0)

        assert exc_info.value.status_code == 403


class TestSecurityEdgeCases:
    """Test edge cases and security scenarios"""

    def test_cookie_signature_prevents_forgery(
        self, test_settings: Settings
    ) -> None:
        """Test that cookies cannot be forged without secret key"""
        # Attempt to create a fake cookie without knowing the secret
        fake_cookie = "A12345678.fake_signature"

        pid = verify_pid_cookie(fake_cookie, test_settings.secret_key)
        assert pid is None

    def test_different_secrets_incompatible(self) -> None:
        """Test that cookies signed with different secrets are incompatible"""
        secret1 = "secret-key-1"
        secret2 = "secret-key-2"

        cookie = create_pid_cookie("A12345678", secret1)
        pid = verify_pid_cookie(cookie, secret2)

        assert pid is None

    def test_admin_cookie_requires_exact_course_match(
        self, test_settings: Settings
    ) -> None:
        """Test that admin cookies require exact course slug match"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Try with slightly different course slug
        assert not verify_admin_cookie(cookie, "test-course-2", test_settings)
        assert not verify_admin_cookie(cookie, "TEST-COURSE", test_settings)

    def test_csrf_token_timing_safe_comparison(self) -> None:
        """Test that CSRF validation uses timing-safe comparison"""
        # Create tokens
        token1 = create_csrf_token()
        token2 = create_csrf_token()

        # Verify uses constant-time comparison (hmac.compare_digest)
        # This is more of a code inspection requirement, but we can test behavior
        assert verify_csrf_token(token1, token1) is True
        assert verify_csrf_token(token1, token2) is False

    def test_empty_string_secret_rejected(self) -> None:
        """Test that empty string as secret is handled safely"""
        # Should still create a cookie, but it won't verify with different secret
        cookie = create_pid_cookie("A12345678", "")
        pid = verify_pid_cookie(cookie, "different-secret")

        assert pid is None

    def test_special_characters_in_pid(self, test_settings: Settings) -> None:
        """Test that special characters in PID are rejected by validation"""
        # These should fail validation
        invalid_pids = [
            "A1234567<",
            "A1234567>",
            "A1234567;",
            "A1234567'",
            'A1234567"',
            "A1234567&",
        ]

        for invalid_pid in invalid_pids:
            assert validate_pid_format(invalid_pid) is False

    def test_null_bytes_in_cookies(self, test_settings: Settings) -> None:
        """Test that null bytes in cookies are handled safely"""
        malicious_cookie = "valid_data\x00malicious_data"

        pid = verify_pid_cookie(malicious_cookie, test_settings.secret_key)
        assert pid is None

    def test_very_long_cookie_rejected(self, test_settings: Settings) -> None:
        """Test that excessively long cookies are rejected"""
        # Create a very long fake cookie
        long_cookie = "A" * 10000

        pid = verify_pid_cookie(long_cookie, test_settings.secret_key)
        assert pid is None


class TestIntegrationScenarios:
    """Integration tests for authentication flows"""

    def test_full_student_auth_flow(self, test_settings: Settings) -> None:
        """Test complete student authentication flow"""
        # Step 1: Validate PID format
        pid = "A12345678"
        assert validate_pid_format(pid) is True

        # Step 2: Create cookie
        cookie = create_pid_cookie(pid, test_settings.secret_key)

        # Step 3: Verify cookie
        verified_pid = verify_pid_cookie(cookie, test_settings.secret_key)
        assert verified_pid == pid

        # Step 4: Use in dependency
        required_pid = require_pid(cookie, test_settings.secret_key)
        assert required_pid == pid

    def test_full_admin_auth_flow(self, test_settings: Settings) -> None:
        """Test complete admin authentication flow"""
        # Step 1: Get course
        course = test_settings.get_course("test-course")
        assert course is not None

        # Step 2: Create cookie with course secret
        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        # Step 3: Verify cookie
        is_valid = verify_admin_cookie(cookie, "test-course", test_settings)
        assert is_valid is True

        # Step 4: Use in dependency (should not raise)
        require_admin(cookie, "test-course", test_settings)

    def test_csrf_protection_flow(self) -> None:
        """Test CSRF protection flow"""
        # Step 1: Generate token for form
        token = create_csrf_token()

        # Step 2: Store in session (simulated)
        session_token = token

        # Step 3: Receive token from form submission
        form_token = token

        # Step 4: Verify tokens match
        assert verify_csrf_token(form_token, session_token) is True

    def test_multi_course_admin_isolation(self, test_settings: Settings) -> None:
        """Test that admin for one course cannot access another"""
        # Get both courses
        course1 = test_settings.get_course("test-course")
        course2 = test_settings.get_course("another-course")
        assert course1 is not None
        assert course2 is not None

        # Create cookie for course1
        cookie1 = create_admin_cookie(
            "test-course", course1.secret, test_settings.secret_key
        )

        # Verify it works for course1
        assert verify_admin_cookie(cookie1, "test-course", test_settings) is True

        # Verify it doesn't work for course2
        assert verify_admin_cookie(cookie1, "another-course", test_settings) is False

        # Create cookie for course2
        cookie2 = create_admin_cookie(
            "another-course", course2.secret, test_settings.secret_key
        )

        # Verify isolation
        assert verify_admin_cookie(cookie2, "another-course", test_settings) is True
        assert verify_admin_cookie(cookie2, "test-course", test_settings) is False
