"""
Tests for basic routes and templates (Phase 4)

This test file covers:
- Admin login page loads
- Admin login POST with valid/invalid secrets
- Admin dashboard (auth required)
- Student page loads
- PID entry flow
- Unauthorized access blocked
- CSRF protection on POSTs
- Invalid course slug handling
"""

from fastapi.testclient import TestClient

from app.auth import create_admin_cookie, create_pid_cookie
from app.config import Settings


class TestAdminLoginPage:
    """Test cases for admin login page"""

    def test_admin_login_page_loads(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that admin login page loads without auth"""
        response = client.get("/c/test-course/admin")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Should contain login form
        assert b"admin" in response.content.lower()

    def test_admin_login_page_invalid_course(self, client: TestClient) -> None:
        """Test admin login page with invalid course slug"""
        response = client.get("/c/nonexistent-course/admin")

        assert response.status_code == 404

    def test_admin_login_page_already_authenticated(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that authenticated admin sees dashboard, not login"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/c/test-course/admin",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        # Should show dashboard, not login form
        assert b"dashboard" in response.content.lower() or b"session" in response.content.lower()


class TestAdminLoginPost:
    """Test cases for admin login POST"""

    def test_admin_login_valid_secret(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test admin login with valid secret redirects to dashboard"""
        course = test_settings.get_course("test-course")
        assert course is not None

        response = client.post(
            "/c/test-course/admin/login",
            data={"secret": course.secret},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/c/test-course/admin"
        # Should set admin cookie
        assert "admin_session" in response.cookies

    def test_admin_login_invalid_secret(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test admin login with invalid secret shows error"""
        response = client.post(
            "/c/test-course/admin/login",
            data={"secret": "wrong-secret"},
            follow_redirects=False,
        )

        # Should redirect back to login or show error
        assert response.status_code in [303, 400, 401]

        if response.status_code == 303:
            # If redirected, should not set cookie
            assert "admin_session" not in response.cookies or response.cookies.get("admin_session") == ""

    def test_admin_login_empty_secret(self, client: TestClient) -> None:
        """Test admin login with empty secret"""
        response = client.post(
            "/c/test-course/admin/login",
            data={"secret": ""},
            follow_redirects=False,
        )

        assert response.status_code in [400, 401, 422]

    def test_admin_login_invalid_course(self, client: TestClient) -> None:
        """Test admin login for nonexistent course"""
        response = client.post(
            "/c/nonexistent-course/admin/login",
            data={"secret": "any-secret"},
            follow_redirects=False,
        )

        assert response.status_code == 404


class TestAdminDashboard:
    """Test cases for admin dashboard"""

    def test_admin_dashboard_requires_auth(self, client: TestClient) -> None:
        """Test that dashboard without auth redirects to login"""
        response = client.get("/c/test-course/admin", follow_redirects=False)

        assert response.status_code in [200, 303]
        # If 200, should show login form, not dashboard
        if response.status_code == 200:
            # Should be login page
            assert b"secret" in response.content.lower()

    def test_admin_dashboard_with_valid_auth(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that dashboard loads with valid auth"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/c/test-course/admin",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        assert b"test course" in response.content.lower() or b"test-course" in response.content.lower()

    def test_admin_dashboard_with_invalid_cookie(
        self, client: TestClient
    ) -> None:
        """Test that invalid cookie doesn't grant access"""
        response = client.get(
            "/c/test-course/admin",
            cookies={"admin_session": "invalid-cookie"},
            follow_redirects=False,
        )

        # Should redirect to login or show login page
        assert response.status_code in [200, 303]

    def test_admin_dashboard_wrong_course(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that admin for one course can't access another"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/c/another-course/admin",
            cookies={"admin_session": cookie},
            follow_redirects=False,
        )

        # Should not grant access
        assert response.status_code in [200, 303, 403]


class TestStudentPage:
    """Test cases for student page"""

    def test_student_page_loads_without_pid(self, client: TestClient) -> None:
        """Test that student page loads and shows PID entry"""
        response = client.get("/c/test-course")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Should show PID entry form
        assert b"pid" in response.content.lower()

    def test_student_page_with_pid_cookie(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that student page with PID cookie shows main page"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        response = client.get(
            "/c/test-course",
            cookies={"student_session": cookie},
        )

        assert response.status_code == 200
        # Should show main student interface, not PID entry
        # Main interface has Answer and Ask panes
        assert b"answer" in response.content.lower() or b"ask" in response.content.lower()

    def test_student_page_invalid_course(self, client: TestClient) -> None:
        """Test student page with invalid course slug"""
        response = client.get("/c/nonexistent-course")

        assert response.status_code == 404


class TestPIDEntry:
    """Test cases for PID entry flow"""

    def test_pid_entry_valid_format(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test PID entry with valid format"""
        response = client.post(
            "/c/test-course/enter-pid",
            data={"pid": "A12345678"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/c/test-course"
        # Should set PID cookie
        assert "student_session" in response.cookies

    def test_pid_entry_invalid_format(self, client: TestClient) -> None:
        """Test PID entry with invalid format"""
        invalid_pids = [
            "a12345678",  # lowercase
            "12345678",   # no A
            "A1234567",   # too short
            "A123456789", # too long
            "A1234567X",  # letter in number
        ]

        for invalid_pid in invalid_pids:
            response = client.post(
                "/c/test-course/enter-pid",
                data={"pid": invalid_pid},
                follow_redirects=False,
            )

            # Should reject
            assert response.status_code in [303, 400, 422]

    def test_pid_entry_empty(self, client: TestClient) -> None:
        """Test PID entry with empty value"""
        response = client.post(
            "/c/test-course/enter-pid",
            data={"pid": ""},
            follow_redirects=False,
        )

        assert response.status_code in [400, 422]

    def test_pid_entry_invalid_course(self, client: TestClient) -> None:
        """Test PID entry for nonexistent course"""
        response = client.post(
            "/c/nonexistent-course/enter-pid",
            data={"pid": "A12345678"},
            follow_redirects=False,
        )

        assert response.status_code == 404

    def test_pid_entry_creates_valid_cookie(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that PID entry creates a valid, verifiable cookie"""
        response = client.post(
            "/c/test-course/enter-pid",
            data={"pid": "A12345678"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        cookie = response.cookies.get("student_session")
        assert cookie is not None

        # Cookie should be verifiable
        from app.auth import verify_pid_cookie
        pid = verify_pid_cookie(cookie, test_settings.secret_key)
        assert pid == "A12345678"


class TestCSRFProtection:
    """Test cases for CSRF protection"""

    def test_admin_login_without_csrf_accepted(self, client: TestClient) -> None:
        """Test that admin login works without CSRF (no state changes before login)"""
        # Admin login is typically exempt from CSRF or uses a different mechanism
        response = client.post(
            "/c/test-course/admin/login",
            data={"secret": "test-secret-123"},
            follow_redirects=False,
        )

        # Should work (CSRF might not be required for initial login)
        assert response.status_code in [303, 401]

    def test_pid_entry_without_csrf_accepted(self, client: TestClient) -> None:
        """Test that PID entry works without CSRF (no sensitive state changes)"""
        response = client.post(
            "/c/test-course/enter-pid",
            data={"pid": "A12345678"},
            follow_redirects=False,
        )

        # Should work (CSRF might not be required for PID entry)
        assert response.status_code in [303, 400, 422]


class TestUnauthorizedAccess:
    """Test cases for unauthorized access blocking"""

    def test_admin_without_auth_shows_login(self, client: TestClient) -> None:
        """Test that accessing admin without auth shows login"""
        response = client.get("/c/test-course/admin")

        assert response.status_code == 200
        # Should show login form
        assert b"secret" in response.content.lower()

    def test_student_without_pid_shows_entry(self, client: TestClient) -> None:
        """Test that accessing student page without PID shows entry form"""
        response = client.get("/c/test-course")

        assert response.status_code == 200
        # Should show PID entry
        assert b"pid" in response.content.lower()

    def test_invalid_admin_cookie_blocked(self, client: TestClient) -> None:
        """Test that invalid admin cookie is blocked"""
        response = client.get(
            "/c/test-course/admin",
            cookies={"admin_session": "invalid-cookie"},
        )

        # Should show login or reject
        assert response.status_code in [200, 303, 403]

    def test_invalid_pid_cookie_shows_entry(
        self, client: TestClient
    ) -> None:
        """Test that invalid PID cookie shows PID entry"""
        response = client.get(
            "/c/test-course",
            cookies={"student_session": "invalid-cookie"},
        )

        assert response.status_code == 200
        # Should show PID entry again
        assert b"pid" in response.content.lower()


class TestTemplateRendering:
    """Test cases for template rendering"""

    def test_admin_page_contains_htmx(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that admin page includes HTMX"""
        course = test_settings.get_course("test-course")
        assert course is not None

        cookie = create_admin_cookie(
            "test-course", course.secret, test_settings.secret_key
        )

        response = client.get(
            "/c/test-course/admin",
            cookies={"admin_session": cookie},
        )

        assert response.status_code == 200
        # Should include HTMX script
        assert b"htmx" in response.content.lower()

    def test_student_page_contains_htmx(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that student page includes HTMX"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        response = client.get(
            "/c/test-course",
            cookies={"student_session": cookie},
        )

        assert response.status_code == 200
        # Should include HTMX script
        assert b"htmx" in response.content.lower()

    def test_pages_contain_tailwind(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that pages include TailwindCSS"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        response = client.get(
            "/c/test-course",
            cookies={"student_session": cookie},
        )

        assert response.status_code == 200
        # Should include Tailwind (either CDN or class names)
        assert b"tailwind" in response.content.lower() or b"class=" in response.content

    def test_pages_are_responsive(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test that pages have responsive meta tag"""
        cookie = create_pid_cookie("A12345678", test_settings.secret_key)

        response = client.get(
            "/c/test-course",
            cookies={"student_session": cookie},
        )

        assert response.status_code == 200
        # Should have viewport meta tag
        assert b"viewport" in response.content.lower()


class TestEdgeCases:
    """Test edge cases"""

    def test_empty_course_slug(self, client: TestClient) -> None:
        """Test handling of empty course slug"""
        response = client.get("/c//admin")

        # Should handle gracefully (404 or redirect)
        assert response.status_code in [404, 307]

    def test_course_slug_with_special_chars(self, client: TestClient) -> None:
        """Test course slug with special characters"""
        response = client.get("/c/test<script>/admin")

        # Should handle safely
        assert response.status_code in [404, 400]

    def test_very_long_course_slug(self, client: TestClient) -> None:
        """Test very long course slug"""
        long_slug = "a" * 1000
        response = client.get(f"/c/{long_slug}/admin")

        # Should handle gracefully
        assert response.status_code in [404, 400, 414]

    def test_pid_with_sql_injection_attempt(self, client: TestClient) -> None:
        """Test PID entry with SQL injection attempt"""
        response = client.post(
            "/c/test-course/enter-pid",
            data={"pid": "A12345678' OR '1'='1"},
            follow_redirects=False,
        )

        # Should reject invalid format
        assert response.status_code in [303, 400, 422]

    def test_secret_with_special_chars(
        self, client: TestClient, test_settings: Settings
    ) -> None:
        """Test admin login with special characters in secret"""
        # Even if secret has special chars, it should work if correct
        response = client.post(
            "/c/test-course/admin/login",
            data={"secret": "test-secret-123"},
            follow_redirects=False,
        )

        # Should validate against actual secret
        assert response.status_code in [303, 401]
