import base64

import pytest
from fastapi.testclient import TestClient

from app.access import COOKIE_NAME
from app.config import Settings
from app.main import create_app

PASSWORD = "local-test-password-only"


@pytest.fixture
def protected(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "protected.db"),
        public_demo=True,
        demo_username="demo",
        demo_password=PASSWORD,
        demo_temporary_data=True,
        memory_backend="sqlite",
        knowledge_backend="local",
        llm_base_url="",
        llm_model="",
        supplier_url="",
        logistics_url="",
        mcp_url="",
    )
    with TestClient(create_app(settings), base_url="https://testserver") as client:
        yield client


def login(client, **extra):
    return client.post(
        "/login", data={"username": "demo", "password": PASSWORD}, follow_redirects=False, **extra
    )


@pytest.mark.parametrize("path", ["/", "/docs", "/openapi.json"])
def test_protected_pages_redirect_to_login_without_browser_prompt(protected, path):
    response = protected.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert "www-authenticate" not in response.headers
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/static/app.js"),
        ("GET", "/static/index.html"),
        ("GET", "/api/dashboard"),
        ("GET", "/api/proposals/session-001"),
        ("POST", "/api/proposals/example/approve"),
        ("POST", "/api/chat/stream"),
        ("GET", "/api/auth/me"),
        ("POST", "/agents/supplier/"),
        ("GET", "/agents/logistics/.well-known/agent-card.json"),
    ],
)
def test_business_endpoints_require_session(protected, method, path):
    response = protected.request(method, path)
    assert response.status_code == 401
    assert response.json()["login_url"] == "/login"
    assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("path", ["/login", "/static/login.css", "/static/login.js", "/api/health"])
def test_login_page_assets_and_health_are_public(protected, path):
    response = protected.get(path)
    assert response.status_code == 200
    assert PASSWORD not in response.text


@pytest.mark.parametrize("credentials", [("demo", "wrong"), ("other", PASSWORD)])
def test_bad_credentials_show_error_in_form(protected, credentials):
    response = protected.post("/login", data=dict(zip(["username", "password"], credentials)))
    assert response.status_code == 401
    assert "用户名或密码不正确" in response.text
    assert '<form action="/login"' in response.text
    assert "www-authenticate" not in response.headers
    assert not protected.cookies.get(COOKIE_NAME)


def test_missing_credentials_show_form_instead_of_json(protected):
    response = protected.post("/login", data={})
    assert response.status_code == 400
    assert "请输入用户名和访问密码" in response.text


def test_username_is_escaped_and_password_is_never_reflected(protected):
    response = protected.post(
        "/login", data={"username": "<script>alert(1)</script>", "password": PASSWORD}
    )
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert PASSWORD not in response.text


def test_form_login_sets_safe_cookie_and_refresh_preserves_session(protected):
    response = login(protected)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert "Max-Age=28800" in cookie
    assert PASSWORD not in cookie
    assert protected.get("/").status_code == 200
    assert protected.get("/").status_code == 200
    assert protected.get("/api/auth/me").json() == {"login_required": True, "username": "demo"}
    assert protected.get("/login", follow_redirects=False).headers["location"] == "/"


def test_authenticated_stream_and_approval_still_work(protected):
    login(protected)
    response = protected.post(
        "/api/chat/stream", json={"session_id": "protected-session", "message": "SKU-1001 补货50件"}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: result" in response.text and "event: error" not in response.text
    proposal = protected.get("/api/proposals/protected-session").json()[0]
    confirmed = protected.post(
        f"/api/proposals/{proposal['id']}/approve", json={"session_id": "protected-session"}
    )
    assert confirmed.json()["result"]["stock"] == 68


def test_logout_revokes_cookie_even_if_old_value_is_replayed(protected):
    login(protected)
    token = protected.cookies.get(COOKIE_NAME)
    response = protected.post("/logout", follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert not protected.cookies.get(COOKIE_NAME)
    replay = protected.get("/api/dashboard", headers={"Cookie": f"{COOKIE_NAME}={token}"})
    assert replay.status_code == 401


def test_expired_logout_still_returns_to_login_page(protected):
    response = protected.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_session_expiry_returns_to_login(protected):
    login(protected)
    protected.app.state.auth.sessions = {key: 0 for key in protected.app.state.auth.sessions}
    assert protected.get("/api/dashboard").status_code == 401
    assert protected.get("/", follow_redirects=False).headers["location"] == "/login"


def test_forged_cookie_cannot_access_business_data(protected):
    response = protected.get("/api/dashboard", headers={"Cookie": f"{COOKIE_NAME}=forged"})
    assert response.status_code == 401


def test_repeated_login_rotates_session(protected):
    login(protected)
    old = protected.cookies.get(COOKIE_NAME)
    login(protected)
    assert old != protected.cookies.get(COOKIE_NAME)
    assert not protected.app.state.auth.valid(old)


def test_login_and_write_requests_reject_other_origins(protected):
    blocked = login(protected, headers={"Origin": "https://other.example"})
    assert blocked.status_code == 403
    assert not protected.cookies.get(COOKIE_NAME)
    assert login(protected, headers={"Origin": "https://testserver"}).status_code == 303
    assert protected.post("/logout", headers={"Origin": "null"}).status_code == 403
    assert protected.get("/api/dashboard").status_code == 200


def test_login_never_redirects_to_external_next_parameter(protected):
    response = protected.post(
        "/login?next=https://other.example",
        data={"username": "demo", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


def test_old_basic_header_does_not_trigger_browser_auth(protected):
    header = "Basic " + base64.b64encode(f"demo:{PASSWORD}".encode()).decode()
    response = protected.get("/", headers={"Authorization": header}, follow_redirects=False)
    assert response.status_code == 303
    assert "www-authenticate" not in response.headers


def test_unprotected_local_mode_still_works(client):
    assert client.get("/api/auth/me").json()["login_required"] is False
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    assert client.get("/api/dashboard").status_code == 200


@pytest.mark.parametrize("password", ["", "too-short"])
def test_online_mode_refuses_missing_or_short_password(password):
    with pytest.raises(ValueError, match="DEMO_PASSWORD"):
        create_app(Settings(_env_file=None, public_demo=True, demo_password=password))
