import json

import pytest


def chat(client, message, session="test-session-01"):
    response = client.post("/api/chat/stream", json={"message": message, "session_id": session})
    assert response.status_code == 200
    events = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        events.append((lines[0][7:], json.loads(lines[1][6:])))
    assert events[0][0] == "start"
    assert events[-1][0] == "done"
    errors = [e for e in events if e[0] == "error"]
    assert not errors, errors
    return next(payload for kind, payload in events if kind == "result")


def approve(client, proposal, session="test-session-01"):
    return client.post(f"/api/proposals/{proposal['id']}/approve", json={"session_id": session})


def test_dashboard_and_static_assets(client):
    data = client.get("/api/dashboard").json()
    assert len(data["products"]) == 6
    assert len(data["orders"]) == 4
    assert client.get("/api/health").json()["model"] == "rule-demo"
    assert "BORDER" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_restock_requires_confirmation_and_is_idempotent(client):
    result = chat(client, "查询 SKU-1001 的库存，并补货50件")
    facts = result["facts"]["product"]
    assert facts["supplier"]["available"] == 240
    assert facts["supplier"]["task_id"]
    assert client.app.state.store.product("SKU-1001")["stock"] == 18
    proposal = result["proposals"][0]
    assert proposal["status"] == "pending"
    assert approve(client, proposal).status_code == 200
    assert approve(client, proposal).json()["result"]["stock"] == 68
    assert client.app.state.store.product("SKU-1001")["stock"] == 68
    assert client.app.state.store.product("SKU-1001")["supplier_stock"] == 190


def test_pending_proposals_are_deduplicated(client):
    first = chat(client, "SKU-1001 补货20件")["proposals"][0]
    second = chat(client, "SKU-1001 补货20件")["proposals"][0]
    assert first["id"] == second["id"]


@pytest.mark.parametrize("quantity", [0, -2, 10001, 999])
def test_invalid_restock_does_not_create_proposal(client, quantity):
    result = chat(client, f"SKU-1001 补货{quantity}件")
    assert result["proposals"] == []
    assert client.app.state.store.product("SKU-1001")["stock"] == 18


def test_shipment_confirmation_updates_order_and_stock_once(client):
    result = chat(client, "查看 CB-2026002 并安排配送")
    assert result["facts"]["order"]["logistics"]["task_id"]
    proposal = result["proposals"][0]
    assert client.app.state.store.order("CB-2026002")["status"] == "paid"
    assert approve(client, proposal).json()["result"]["tracking"].startswith("DEMO-")
    assert approve(client, proposal).status_code == 200
    assert client.app.state.store.product("SKU-1002")["stock"] == 85
    again = chat(client, "CB-2026002 安排发货")
    assert again["proposals"] == []


def test_session_cannot_approve_other_sessions_proposal(client):
    proposal = chat(client, "SKU-1001 补货20件")["proposals"][0]
    assert approve(client, proposal, "other-session").status_code == 404
    assert client.app.state.store.product("SKU-1001")["stock"] == 18


def test_memory_reuses_product_and_country_but_isolates_sessions(client):
    chat(client, "SKU-1002 德国市场营销")
    result = chat(client, "再写一份营销文案")
    assert result["facts"]["marketing"]["market"] == "DE"
    assert "de" in result["facts"]["marketing"]["drafts"]
    other = chat(client, "写营销文案", "other-session")
    assert "drafts" not in other["facts"]["marketing"]


def test_support_sources_are_traceable_and_product_scoped(client):
    result = chat(client, "SKU-1001 退货和评论")
    sources = result["facts"]["support"]["sources"]
    assert sources and all(s["source"] for s in sources)
    assert all(not s.get("sku") or s["sku"] == "SKU-1001" for s in sources)
    assert "policy-return" in [s["source"] for s in sources]


@pytest.mark.parametrize(
    "message,key", [("SKU-9999 库存", "product"), ("CB-9999999 订单", "order")]
)
def test_unknown_entities_do_not_fall_back_to_another_product(client, message, key):
    result = chat(client, message)
    assert "不存在" in result["facts"][key]["message"]
    assert result["proposals"] == []


def test_multi_agent_request_shares_order_context(client):
    result = chat(client, "查看 CB-2026002 订单，并生成德国市场营销文案和退货政策")
    assert set(result["facts"]) == {"order", "support", "marketing"}
    assert result["facts"]["marketing"]["market"] == "DE"
    assert "Nomad" in result["facts"]["marketing"]["drafts"]["en"]


@pytest.mark.parametrize("message", ["", "   ", "x" * 2001])
def test_invalid_messages_are_rejected(client, message):
    assert (
        client.post(
            "/api/chat/stream", json={"message": message, "session_id": "test-session"}
        ).status_code
        == 422
    )


def test_session_busy_is_rejected(client):
    client.app.state.active_sessions.add("test-session")
    assert (
        client.post(
            "/api/chat/stream", json={"message": "库存", "session_id": "test-session"}
        ).status_code
        == 409
    )


def test_agent_card_and_jsonrpc_are_served(client):
    card = client.get("/agents/supplier/.well-known/agent-card.json")
    assert card.status_code == 200
    assert card.json()["protocolVersion"] == "0.3.0"
    assert card.json()["url"] == "http://testserver/agents/supplier/"
    response = client.post(
        "/agents/supplier/",
        json={
            "jsonrpc": "2.0",
            "id": "test-rpc",
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": "msg-1",
                    "parts": [{"kind": "text", "text": '{"sku":"SKU-1001"}'}],
                }
            },
        },
    )
    task = response.json()["result"]
    assert task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"][0]["data"]["available"] == 240


def test_approval_rechecks_supplier_stock_across_sessions(client):
    first = chat(client, "SKU-1001 补货200件", "session-first")["proposals"][0]
    second = chat(client, "SKU-1001 补货200件", "session-second")["proposals"][0]
    assert approve(client, first, "session-first").status_code == 200
    assert approve(client, second, "session-second").status_code == 409
    assert client.app.state.store.product("SKU-1001")["supplier_stock"] == 40


def test_two_sessions_cannot_ship_one_order_twice(client):
    first = chat(client, "CB-2026002 安排配送", "session-first")["proposals"][0]
    second = chat(client, "CB-2026002 安排配送", "session-second")["proposals"][0]
    assert approve(client, first, "session-first").status_code == 200
    assert approve(client, second, "session-second").status_code == 409
    assert client.app.state.store.product("SKU-1002")["stock"] == 85
