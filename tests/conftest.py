import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        database_path=str(tmp_path / "commerce.db"),
        memory_backend="sqlite",
        knowledge_backend="local",
        supplier_url="",
        logistics_url="",
        mcp_url="",
        llm_base_url="",
        llm_model="",
    )
    with TestClient(create_app(settings)) as client:
        yield client
