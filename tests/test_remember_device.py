"""
Tests for "Remember this device" on the admin login

This test file covers:
- The remember token: valid, expired, revoked (version bumped), wrong course,
  admin secret changed, tampered, signed with another key
- Login with and without the checkbox (cookie attributes, unchanged default)
- The Secure flag: request scheme, Origin, Referer
- A remembered device can use admin routes without the password
- Revoking all remembered devices, and that the revocation survives session stops
"""

import time
from http.cookies import SimpleCookie

import pytest
import redis
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import (
    create_remember_token,
    remember_cookie_name,
    verify_remember_token,
)
from app.config import Settings
from app.redis_client import RedisClient

COURSE = "test-course"
SECRET = "test-secret-123"
COOKIE = remember_cookie_name(COURSE)


def token_for(settings: Settings, course: str = COURSE, version: int = 0) -> str:
    config = settings.get_course(course)
    assert config is not None
    return create_remember_token(course, config.secret, version, settings.secret_key)


def set_cookie_headers(response) -> dict[str, SimpleCookie]:
    """Parse each Set-Cookie header of a response, by cookie name"""
    cookies = {}
    for header in response.headers.get_list("set-cookie"):
        c: SimpleCookie = SimpleCookie()
        c.load(header)
        for name in c:
            cookies[name] = c
    return cookies


def login(client: TestClient, remember: bool, headers: dict[str, str] | None = None):
    data = {"secret": SECRET}
    if remember:
        data["remember"] = "1"
    return client.post(
        f"/{COURSE}/admin/login", data=data, headers=headers or {}, follow_redirects=False
    )


class TestRememberToken:
    def test_valid(self, test_settings: Settings) -> None:
        assert verify_remember_token(token_for(test_settings), COURSE, 0, test_settings)

    def test_missing(self, test_settings: Settings) -> None:
        assert not verify_remember_token(None, COURSE, 0, test_settings)
        assert not verify_remember_token("", COURSE, 0, test_settings)

    def test_expired(self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        test_settings.admin_remember_days = 30
        token = token_for(test_settings)
        now = time.time()

        monkeypatch.setattr(time, "time", lambda: now + 29 * 86400)
        assert verify_remember_token(token, COURSE, 0, test_settings)

        monkeypatch.setattr(time, "time", lambda: now + 31 * 86400)
        assert not verify_remember_token(token, COURSE, 0, test_settings)

    def test_revoked(self, test_settings: Settings) -> None:
        token = token_for(test_settings, version=0)
        assert not verify_remember_token(token, COURSE, 1, test_settings)

    def test_new_version_valid_after_revoke(self, test_settings: Settings) -> None:
        assert verify_remember_token(token_for(test_settings, version=3), COURSE, 3, test_settings)

    def test_wrong_course(self, test_settings: Settings) -> None:
        token = token_for(test_settings, course="another-course")
        assert not verify_remember_token(token, COURSE, 0, test_settings)
        assert verify_remember_token(token, "another-course", 0, test_settings)

    def test_unknown_course(self, test_settings: Settings) -> None:
        assert not verify_remember_token(token_for(test_settings), "nope", 0, test_settings)

    def test_admin_secret_changed(self, test_settings: Settings) -> None:
        token = token_for(test_settings)
        test_settings.get_course(COURSE).secret = "a-new-secret"  # type: ignore[union-attr]
        assert not verify_remember_token(token, COURSE, 0, test_settings)

    def test_other_signing_key(self, test_settings: Settings) -> None:
        token = create_remember_token(COURSE, SECRET, 0, "some-other-key")
        assert not verify_remember_token(token, COURSE, 0, test_settings)

    def test_tampered(self, test_settings: Settings) -> None:
        token = token_for(test_settings)
        tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
        assert not verify_remember_token(tampered, COURSE, 0, test_settings)
        assert not verify_remember_token("garbage", COURSE, 0, test_settings)

    def test_token_does_not_contain_admin_secret(self, test_settings: Settings) -> None:
        import base64

        payload = token_for(test_settings).split(".")[0]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        assert SECRET.encode() not in decoded

    def test_admin_session_cookie_is_not_a_remember_token(self, test_settings: Settings) -> None:
        from app.auth import create_admin_cookie

        cookie = create_admin_cookie(COURSE, SECRET, test_settings.secret_key)
        assert not verify_remember_token(cookie, COURSE, 0, test_settings)


class TestLogin:
    def test_without_remember_is_unchanged(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        response = login(client, remember=False)

        assert response.status_code == 303
        assert response.headers["location"] == f"/{COURSE}/admin"
        cookies = set_cookie_headers(response)
        assert set(cookies) == {"admin_session"}
        morsel = cookies["admin_session"]["admin_session"]
        assert morsel["max-age"] == "86400"
        assert morsel["httponly"]
        assert morsel["samesite"].lower() == "lax"
        assert not morsel["secure"]

    def test_with_remember_sets_long_lived_cookie(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        response = login(client, remember=True)

        assert response.status_code == 303
        cookies = set_cookie_headers(response)
        assert set(cookies) == {"admin_session", COOKIE}
        morsel = cookies[COOKIE][COOKIE]
        assert morsel["max-age"] == str(365 * 86400)
        assert morsel["httponly"]
        assert morsel["samesite"].lower() == "lax"
        assert morsel["path"] == "/"
        assert not morsel["secure"]
        assert verify_remember_token(morsel.value, COURSE, 0, test_settings)

    def test_remember_days_setting(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        test_settings.admin_remember_days = 30
        morsel = set_cookie_headers(login(client, remember=True))[COOKIE][COOKIE]
        assert morsel["max-age"] == str(30 * 86400)

    def test_wrong_password_with_remember_sets_nothing(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        response = client.post(
            f"/{COURSE}/admin/login",
            data={"secret": "wrong", "remember": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert response.headers.get_list("set-cookie") == []

    def test_cookie_path_is_root_path(self, client: TestClient, redis_client) -> None:
        qa_client = TestClient(client.app, root_path="/qa")
        response = qa_client.post(
            f"/qa/{COURSE}/admin/login",
            data={"secret": SECRET, "remember": "1"},
            follow_redirects=False,
        )
        assert response.headers["location"] == f"/qa/{COURSE}/admin"
        assert set_cookie_headers(response)[COOKIE][COOKIE]["path"] == "/qa/"

    def test_login_page_has_checkbox_unchecked(self, client: TestClient) -> None:
        html = client.get(f"/{COURSE}/admin").text
        assert 'name="remember"' in html
        assert "Remember this device" in html
        assert "checked" not in html


class TestSecureFlag:
    @pytest.mark.parametrize(
        ("headers", "secure"),
        [
            ({}, False),
            ({"origin": "http://example.test"}, False),
            ({"origin": "https://example.test"}, True),
            ({"origin": "null", "referer": "https://example.test/qa/x/admin"}, True),
            ({"referer": "https://example.test/qa/x/admin"}, True),
            ({"referer": "http://example.test/qa/x/admin"}, False),
            # Origin wins over Referer
            ({"origin": "http://example.test", "referer": "https://example.test/"}, False),
        ],
    )
    def test_from_browser_headers(
        self,
        client: TestClient,
        redis_client: redis.Redis,
        headers: dict[str, str],
        secure: bool,
    ) -> None:
        morsel = set_cookie_headers(login(client, True, headers))[COOKIE][COOKIE]
        assert bool(morsel["secure"]) is secure

    def test_from_request_scheme(self, client: TestClient, redis_client) -> None:
        """HTTPS as seen by the app (e.g. X-Forwarded-Proto: https through a trusted proxy)"""
        https_client = TestClient(client.app, base_url="https://testserver")
        response = login(https_client, True)
        assert set_cookie_headers(response)[COOKIE][COOKIE]["secure"]

    def test_proxy_saying_http_but_browser_on_https(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        """HTTPS ended in front of the proxy: the proxy reports http, the browser's Origin https"""
        headers = {"x-forwarded-proto": "http", "origin": "https://example.test"}
        morsel = set_cookie_headers(login(client, True, headers))[COOKIE][COOKIE]
        assert morsel["secure"]


class TestRememberedDevice:
    def remembered_client(self, client: TestClient, test_settings: Settings) -> TestClient:
        client.cookies.clear()
        client.cookies.set(COOKIE, token_for(test_settings))
        return client

    def test_can_use_admin_without_password(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        c = self.remembered_client(client, test_settings)
        page = c.get(f"/{COURSE}/admin")
        assert "Admin Dashboard" in page.text
        assert "This device is remembered" in page.text
        assert c.get(f"/{COURSE}/admin/state").status_code == 200
        assert c.post(f"/{COURSE}/admin/session/start").status_code == 200

    def test_login_then_remembered_after_session_cookie_gone(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        response = login(client, remember=True)
        remember_value = set_cookie_headers(response)[COOKIE][COOKIE].value
        client.cookies.clear()
        client.cookies.set(COOKIE, remember_value)
        assert client.get(f"/{COURSE}/admin/state").status_code == 200

    def test_wrong_course(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        client.cookies.clear()
        # another-course's token, even under this course's cookie name
        client.cookies.set(COOKIE, token_for(test_settings, course="another-course"))
        assert client.get(f"/{COURSE}/admin/state").status_code == 403
        assert "Admin Login" in client.get(f"/{COURSE}/admin").text

    def test_does_not_carry_over_to_other_course(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        c = self.remembered_client(client, test_settings)
        assert c.get("/another-course/admin/state").status_code == 403

    def test_expired(
        self,
        client: TestClient,
        redis_client: redis.Redis,
        test_settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        c = self.remembered_client(client, test_settings)
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + 366 * 86400)
        assert c.get(f"/{COURSE}/admin/state").status_code == 403

    def test_revoke_all(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        c = self.remembered_client(client, test_settings)
        other_device = token_for(test_settings)

        response = c.post(f"/{COURSE}/admin/remembered-devices/revoke", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"/{COURSE}/admin"
        # This device's cookie is cleared
        morsel = set_cookie_headers(response)[COOKIE][COOKIE]
        assert morsel["max-age"] == "0"

        # Old tokens (this device's and others') no longer work
        c.cookies.clear()
        c.cookies.set(COOKIE, other_device)
        assert c.get(f"/{COURSE}/admin/state").status_code == 403

        # Logging in with "remember" again issues a token that works
        response = login(c, remember=True)
        fresh = set_cookie_headers(response)[COOKIE][COOKIE].value
        c.cookies.clear()
        c.cookies.set(COOKIE, fresh)
        assert c.get(f"/{COURSE}/admin/state").status_code == 200

    def test_revoke_only_affects_that_course(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        RedisClient(redis_client).revoke_admin_remembered_devices(COURSE)
        other = remember_cookie_name("another-course")
        client.cookies.set(other, token_for(test_settings, course="another-course"))
        assert client.get("/another-course/admin/state").status_code == 200

    def test_revoke_keeps_password_sessions(
        self, client: TestClient, redis_client: redis.Redis
    ) -> None:
        login(client, remember=False)
        assert (
            client.post(
                f"/{COURSE}/admin/remembered-devices/revoke", follow_redirects=False
            ).status_code
            == 303
        )
        assert client.get(f"/{COURSE}/admin/state").status_code == 200

    def test_revoke_requires_admin(self, client: TestClient, redis_client: redis.Redis) -> None:
        response = client.post(f"/{COURSE}/admin/remembered-devices/revoke")
        assert response.status_code == 403
        assert RedisClient(redis_client).get_admin_remember_version(COURSE) == 0

    def test_revocation_survives_session_stop(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        """Stopping a session clears course:<course>:* keys; the version must not be among them"""
        old = token_for(test_settings, version=0)
        rc = RedisClient(redis_client)
        rc.revoke_admin_remembered_devices(COURSE)
        rc.start_session(COURSE)
        rc.clear_session_data(COURSE)
        rc.apply_ttl_to_course_keys(COURSE, 1)

        assert rc.get_admin_remember_version(COURSE) == 1
        assert redis_client.ttl(rc.admin_remember_version_key(COURSE)) == -1
        assert not verify_remember_token(
            old, COURSE, rc.get_admin_remember_version(COURSE), test_settings
        )

    def test_tampered_cookie(self, client: TestClient, redis_client: redis.Redis) -> None:
        client.cookies.set(COOKIE, "eyJjIjoidGVzdC1jb3Vyc2UiLCJ2IjowfQ.bogus.sig")
        assert client.get(f"/{COURSE}/admin/state").status_code == 403

    def test_admin_sse_accepts_remembered_device(
        self, client: TestClient, redis_client: redis.Redis, test_settings: Settings
    ) -> None:
        from app.routes import sse

        class FakeRequest:
            cookies = {COOKIE: token_for(test_settings)}

        sse.verify_admin_auth(FakeRequest(), COURSE)  # type: ignore[arg-type]
        FakeRequest.cookies = {}
        with pytest.raises(HTTPException) as exc_info:
            sse.verify_admin_auth(FakeRequest(), COURSE)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 403
