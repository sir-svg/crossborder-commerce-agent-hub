import base64

import pytest
from fastapi.testclient import TestClient

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
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/"),
        ("GET", "/static/app.js"),
        ("GET", "/api/dashboard"),
        ("GET", "/api/proposals/session-001"),
        ("POST", "/api/proposals/example/approve"),
        ("POST", "/api/chat/stream"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("POST", "/agents/supplier/"),
        ("GET", "/agents/logistics/.well-known/agent-card.json"),
    ],
)
def test_entire_demo_requires_authentication(protected, method, path):
    response = protected.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("credentials", [("demo", "wrong"), ("other", PASSWORD)])
def test_bad_credentials_are_rejected(protected, credentials):
    assert protected.get("/api/dashboard", auth=credentials).status_code == 401


@pytest.mark.parametrize(
    "authorization", ["Basic ?!", "Bearer test", "Basic " + base64.b64encode(b"no-colon").decode()]
)
def test_malformed_credentials_do_not_crash(protected, authorization):
    assert protected.get("/", headers={"Authorization": authorization}).status_code == 401


def test_health_is_public_but_never_contains_password(protected):
    response = protected.get("/api/health")
    assert response.status_code == 200
    assert response.json()["temporary_data"] is True
    assert PASSWORD not in response.text


def test_authenticated_stream_and_approval_still_work(protected):
    auth = ("demo", PASSWORD)
    assert protected.get("/", auth=auth).status_code == 200
    assert protected.get("/static/app.js", auth=auth).status_code == 200
    response = protected.post(
        "/api/chat/stream",
        auth=auth,
        json={"session_id": "protected-session", "message": "SKU-1001 补货50件"},
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: result" in response.text
    assert "event: error" not in response.text
    proposals = protected.get("/api/proposals/protected-session", auth=auth).json()
    assert len(proposals) == 1
    confirmed = protected.post(
        f"/api/proposals/{proposals[0]['id']}/approve",
        auth=auth,
        json={"session_id": "protected-session"},
    )
    assert confirmed.json()["result"]["stock"] == 68


@pytest.mark.parametrize("password", ["", "too-short"])
def test_online_mode_refuses_missing_or_short_password(password):
    with pytest.raises(ValueError, match="DEMO_PASSWORD"):
        create_app(Settings(_env_file=None, public_demo=True, demo_password=password))
