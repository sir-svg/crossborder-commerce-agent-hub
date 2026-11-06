import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import Settings
from app.llm import Model, ModelError

KEY = "test-secret-only-not-a-real-key"


def model(transport):
    return Model(
        Settings(
            _env_file=None,
            llm_api_key=KEY,
            llm_base_url="https://model.test/v1",
            llm_model="test-model",
        ),
        transport=httpx.MockTransport(transport),
    )


def completion(value):
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        },
    )


def stream_response(text="模型已完成分析，请确认演示操作。"):
    events = [
        {"choices": [{"delta": {"reasoning_content": "private-reasoning-not-for-ui"}}]},
        {"choices": [{"delta": {"content": text[:5]}}]},
        {"choices": [{"delta": {"content": text[5:]}}]},
        {
            "model": "test-model",
            "choices": [],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        },
    ]
    content = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n" for event in events)
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=content + "data: [DONE]\n\n"
    )


@pytest.mark.asyncio
async def test_structured_reply_is_validated_and_secret_stays_out_of_payload():
    class Answer(BaseModel):
        ok: bool

    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert KEY not in request.content.decode()
        assert request.headers["authorization"] == "Bearer " + KEY
        return completion({"ok": True})

    ai = model(handler)
    usage = []
    assert (await ai.structured("返回JSON", {}, Answer, usage=usage)).ok is True
    assert usage[0]["total_tokens"] == 20
    assert ai.status()["status"] == "ready"
    assert KEY not in json.dumps(ai.status())
    assert KEY not in repr(ai.settings)


@pytest.mark.asyncio
async def test_authentication_failure_is_redacted_and_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": {"message": "invalid key: " + KEY}})

    ai = model(handler)
    with pytest.raises(ModelError) as caught:
        await ai.complete("test", {})
    assert caught.value.code == "auth"
    assert KEY not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_temporary_failure_has_one_bounded_retry():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else completion({"ok": True})

    assert await model(handler).complete("test", {})
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_timeout_and_bad_model_reply_have_safe_errors():
    def timeout(request):
        raise httpx.ReadTimeout("sensitive data " + KEY)

    with pytest.raises(ModelError, match="超时"):
        await model(timeout).complete("test", {})
    with pytest.raises(ModelError) as bad:
        await model(lambda request: httpx.Response(200, json={"not": "a completion"})).complete(
            "test", {}
        )
    assert bad.value.code == "invalid_reply"


@pytest.mark.asyncio
async def test_model_stream_only_emits_answer_and_records_actual_usage():
    ai = model(lambda request: stream_response("实际生成的内容"))
    usage = []
    text = "".join([part async for part in ai.stream("test", {}, usage=usage)])
    assert text == "实际生成的内容"
    assert "private-reasoning" not in text
    assert usage[0]["total_tokens"] == 20


@pytest.mark.asyncio
async def test_truncated_model_stream_is_rejected():
    response = httpx.Response(
        200,
        content='data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n',
    )
    with pytest.raises(ModelError, match="格式"):
        _ = [text async for text in model(lambda request: response).stream("test", {})]


def install_model(client, plan, analysis=None, summary="模型已完成分析，请确认演示操作。"):
    captured = []
    analysis = analysis or {"summary": "已根据工具事实分析。", "citations": [], "drafts": {}}

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        if body.get("stream"):
            return stream_response(summary)
        system = body["messages"][0]["content"]
        return completion(plan if "任务规划器" in system else analysis)

    client.app.state.workflow.model = model(handler)
    return captured


def chat(client, text, session="model-flow-session"):
    response = client.post("/api/chat/stream", json={"message": text, "session_id": session})
    assert response.status_code == 200
    frames = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        frames.append((lines[0][7:], json.loads(lines[1][6:])))
    assert not [value for event, value in frames if event == "error"]
    return next(value for event, value in frames if event == "result"), frames


def test_chinese_quantity_model_plan_creates_only_pending_proposal(client):
    message = "给降噪耳机再进五十个，先生成确认单"
    install_model(
        client,
        {
            "agents": ["product"],
            "sku": "SKU-1001",
            "quantity": 50,
            "restock": True,
            "action_evidence": message,
        },
    )
    result, events = chat(client, message)
    assert result["proposals"][0]["payload"]["quantity"] == 50
    assert client.app.state.store.product("SKU-1001")["stock"] == 18
    assert result["model_usage"]["calls"] == 3
    assert result["model_usage"]["total_tokens"] == 60
    assert any(event == "token" for event, _ in events)
    assert "尚未执行" in result["answer"]


def test_target_stock_means_top_up_not_add_that_amount(client):
    message = "给旅行背包补到一百件"
    install_model(
        client,
        {
            "agents": ["product"],
            "sku": "SKU-1002",
            "target_stock": 100,
            "restock": True,
            "action_evidence": message,
        },
    )
    result, _ = chat(client, message)
    assert result["proposals"][0]["payload"]["quantity"] == 14


@pytest.mark.parametrize(
    "message,evidence",
    [
        ("不要给耳机补货50件，只查库存", "不要给耳机补货50件"),
        ("把耳机补货50件翻译成英文", "耳机补货50件"),
        ("查看耳机库存", "model invented restock"),
    ],
)
def test_model_cannot_turn_negation_translation_or_invented_evidence_into_drafts(
    client, message, evidence
):
    install_model(
        client,
        {
            "agents": ["product"],
            "sku": "SKU-1001",
            "quantity": 50,
            "restock": True,
            "action_evidence": evidence,
        },
    )
    result, _ = chat(client, message)
    assert result["proposals"] == []


def test_missing_quantity_is_asked_instead_of_guessed_from_history(client):
    message = "给耳机补点货"
    install_model(
        client,
        {
            "agents": ["product"],
            "sku": "SKU-1001",
            "quantity": 20,
            "restock": True,
            "action_evidence": message,
        },
    )
    result, _ = chat(client, message)
    assert result["proposals"] == []
    assert "需要补充" in result["answer"]


def test_explicit_unknown_id_cannot_be_silently_replaced_by_model(client):
    message = "给 SKU-9999 补货50件"
    install_model(
        client,
        {
            "agents": ["product"],
            "sku": "SKU-1001",
            "quantity": 50,
            "restock": True,
            "action_evidence": message,
        },
    )
    result, _ = chat(client, message)
    assert result["proposals"] == []
    assert "不存在" in result["facts"]["product"]["message"]


def test_model_drafts_replace_templates_with_required_market_languages(client):
    drafts = {
        "zh": "背上轻便，出发通勤。",
        "en": "Pack light. Move freely.",
        "de": "Leicht packen. Frei unterwegs.",
    }
    install_model(
        client,
        {"agents": ["marketing"], "sku": "SKU-1002", "country": "DE"},
        {"summary": "面向德国市场的通勤草稿。", "citations": [], "drafts": drafts},
    )
    result, _ = chat(client, "为旅行背包写一份德国推广文案")
    assert result["facts"]["marketing"]["drafts"] == drafts
    assert result["facts"]["marketing"]["model_generated"] is True


def test_invented_citations_are_rejected_and_facts_remain(client):
    install_model(
        client,
        {"agents": ["support"], "sku": "SKU-1001"},
        {"summary": "未知政策[policy-invented]。", "citations": ["policy-invented"], "drafts": {}},
    )
    result, _ = chat(client, "耳机退货政策")
    assert not result["facts"]["support"].get("model_generated")
    assert result["model_usage"]["degraded"] is True
    assert any(doc["source"] == "policy-return" for doc in result["facts"]["support"]["sources"])


def test_summary_with_invented_source_resets_stream_and_preserves_real_sources(client):
    install_model(
        client,
        {"agents": ["support"], "sku": "SKU-1001"},
        summary="这不是实际检索到的来源[policy-invented]。",
    )
    result, events = chat(client, "耳机退货政策")
    assert any(kind == "answer_reset" for kind, _ in events)
    assert "policy-invented" not in result["answer"]
    assert "policy-return" in result["answer"]
    assert result["model_usage"]["degraded"] is True


def test_unique_unshipped_market_order_can_be_resolved_without_id(client):
    message = "把还没发货的德国订单安排配送，给我确认单"
    install_model(
        client, {"agents": ["order"], "country": "DE", "ship": True, "action_evidence": message}
    )
    result, _ = chat(client, message)
    assert result["proposals"][0]["payload"] == {"order_id": "CB-2026002"}
    assert client.app.state.store.order("CB-2026002")["status"] == "paid"


def test_full_dialogue_is_saved_and_given_to_next_model_request(client):
    captured = install_model(client, {"agents": ["product"], "sku": "SKU-1001"})
    first, _ = chat(client, "查一下降噪耳机")
    second, _ = chat(client, "它适合通勤吗")
    plans = [
        json.loads(body["messages"][1]["content"])
        for body in captured
        if "任务规划器" in body["messages"][0]["content"]
    ]
    assert plans[1]["recent_dialogue"][0]["question"] == "查一下降噪耳机"
    history = client.get("/api/history/model-flow-session").json()
    assert len(history) == 2 and history[-1]["answer"] == second["answer"]
    assert client.get("/api/history/other-session").json() == []
    assert KEY not in json.dumps(history)
    assert KEY not in client.get("/api/model/status").text
    assert first["model_usage"]["mode"] == "model"
