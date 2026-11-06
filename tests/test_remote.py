"""通过真实 HTTP 监听验证分离的 A2A Agent 和 MCP 工具服务。"""

import socket
import threading
import time
from contextlib import contextmanager

import httpx
import pytest
import uvicorn

from app.config import Settings
from app.external import A2AGateway, DelegationError, create_external
from app.knowledge import Knowledge
from app.memory import Memory
from app.store import Store
from app.tools import ToolGateway, create_tools


@contextmanager
def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("test service did not start")
            time.sleep(0.03)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


@pytest.mark.asyncio
async def test_separate_mcp_and_a2a_over_http(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "remote.db"))
    store = Store(settings.database_path)
    knowledge = Knowledge(settings, store)
    tools = create_tools(store, knowledge)
    with serve(tools.http_app()) as tools_url:
        gateway = ToolGateway(tools_url + "/mcp")
        products = await gateway.call("search_products", {"query": "SKU-1001"})
        assert products[0]["stock"] == 18
        supplier = create_external("supplier", gateway, "http://unused.local")
        logistics = create_external("logistics", gateway, "http://unused.local")
        with serve(supplier) as supplier_url, serve(logistics) as logistics_url:
            external = A2AGateway({}, {"supplier": supplier_url, "logistics": logistics_url})
            inventory = await external.delegate("supplier", {"sku": "SKU-1001"})
            assert inventory["available"] == 240
            quote = await external.delegate("logistics", {"sku": "SKU-1002", "country": "DE"})
            assert quote["cost_usd"] > 0
            assert quote["tax_included"] is False
            with pytest.raises(DelegationError):
                await external.delegate("logistics", {"sku": "SKU-1001", "country": "XX"})


@pytest.mark.asyncio
async def test_memory_survives_recreating_sqlite_adapter(tmp_path):
    path = str(tmp_path / "memory.db")
    settings = Settings(_env_file=None, database_path=path, memory_backend="sqlite")
    memory = Memory(settings, Store(path))
    await memory.save("session-001", {"sku": "SKU-1001", "country": "JP"})
    await memory.close()
    reopened = Memory(settings, Store(path))
    assert await reopened.load("session-001") == {"sku": "SKU-1001", "country": "JP"}
    assert await reopened.load("session-002") == {}
    await reopened.close()


def test_supplier_outage_preserves_local_product_and_prevents_proposal(client):
    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("supplier unavailable")

    client.app.state.workflow.external.delegate = unavailable
    response = client.post(
        "/api/chat/stream", json={"session_id": "outage-session", "message": "SKU-1001 补货20件"}
    )
    assert "event: result" in response.text
    assert '"proposals": []' in response.text
    assert client.app.state.store.product("SKU-1001")["stock"] == 18


def test_llm_unavailable_falls_back_without_losing_facts(client):
    async def unavailable(*args, **kwargs):
        raise httpx.ConnectError("model unavailable")

    model = client.app.state.workflow.model
    model.enabled = True
    model.complete = unavailable
    response = client.post(
        "/api/chat/stream", json={"session_id": "model-session", "message": "SKU-1001 库存"}
    )
    assert "event: result" in response.text
    assert '"available": 240' in response.text
    assert "event: error" not in response.text
