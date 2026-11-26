from unittest.mock import AsyncMock

import pytest
from test_business import approve, chat


def module(client, name, **body):
    return client.post(f"/api/modules/{name}", json={"session_id": "test-session-01", **body})


def test_product_operations_share_confirmations_and_skip_planner(client, monkeypatch):
    workflow = client.app.state.workflow
    monkeypatch.setattr(workflow.model, "structured", AsyncMock(side_effect=AssertionError()))
    result = module(client, "product", sku="SKU-1001").json()
    assert result["facts"]["supplier"]["available"] == 240
    assert result["proposals"] == []
    draft = module(client, "product", sku="SKU-1001", action="restock", target_stock=68).json()
    proposal = draft["proposals"][0]
    assert proposal["payload"]["quantity"] == 50
    assert draft["facts"]["supplier"]["request"]["quantity"] == 50
    assert client.app.state.store.product("SKU-1001")["stock"] == 18
    assert client.get("/api/proposals/test-session-01").json()[0]["id"] == proposal["id"]
    assert approve(client, proposal).status_code == 200
    assert client.app.state.store.product("SKU-1001")["stock"] == 68
    assert approve(client, proposal).json()["result"]["stock"] == 68
    assert not workflow.model.structured.called


@pytest.mark.parametrize(
    "extra", [{}, {"quantity": 0}, {"quantity": 1, "target_stock": 20}, {"quantity": "10"}]
)
def test_direct_restock_validates_parameters(client, extra):
    response = module(client, "product", sku="SKU-1001", action="restock", **extra)
    assert response.status_code == 422
    assert client.get("/api/proposals/test-session-01").json() == []


def test_order_quote_and_shipment_share_chat_workflow(client):
    quote = module(client, "order", order_id="CB-2026002").json()
    assert quote["facts"]["order"]["status"] == "paid"
    assert quote["facts"]["logistics"]["task_id"]
    assert quote["proposals"] == []
    proposal = chat(client, "CB-2026002 安排配送")["proposals"][0]
    direct = module(client, "order", order_id="CB-2026002", action="shipment").json()
    assert direct["proposals"][0]["id"] == proposal["id"]
    assert approve(client, proposal).status_code == 200
    assert not module(client, "order", order_id="CB-2026002", action="shipment").json()["proposals"]


def test_support_and_marketing_work_without_chat_memory(client):
    chat(client, "SKU-1001 查询库存")
    original = client.app.state.store.load_memory("test-session-01")
    result = module(client, "support", sku="SKU-1002", topic="reviews").json()
    assert [d["source"] for d in result["facts"]["sources"]] == ["review-1002"]
    policy = module(client, "support", topic="policy").json()
    assert [d["source"] for d in policy["facts"]["sources"]] == ["policy-return"]
    marketing = module(client, "marketing", sku="SKU-1002", country="DE").json()
    assert {"zh", "en", "de"} <= set(marketing["facts"]["drafts"])
    assert marketing["facts"]["product"]["sku"] == "SKU-1002"
    assert client.app.state.store.load_memory("test-session-01") == original
    history = client.get("/api/history/test-session-01").json()
    assert history[-1]["metadata"]["module"] == "marketing"
    assert module(client, "marketing", sku="SKU-1005", country="DE").status_code == 422
    assert module(client, "product", sku="SKU-9999").status_code == 404


def test_direct_marketing_uses_one_model_call(client):
    from test_model import completion, model

    calls = []

    def handler(request):
        calls.append(request)
        return completion(
            {
                "summary": "独立模块文案",
                "citations": [],
                "drafts": {"zh": "中文草稿", "en": "English", "de": "Deutsch"},
            }
        )

    client.app.state.workflow.model = model(handler)
    result = module(client, "marketing", sku="SKU-1002", country="DE").json()
    assert result["facts"]["drafts"]["de"] == "Deutsch"
    assert result["model_usage"]["calls"] == len(calls) == 1
    assert result["model_usage"]["mode"] == "model"


def test_module_and_chat_cannot_write_same_active_session(client):
    client.app.state.active_sessions.add("test-session-01")
    try:
        assert module(client, "product", sku="SKU-1001").status_code == 409
    finally:
        client.app.state.active_sessions.clear()
