"""Typed business entry points for the four independent workspaces."""

import asyncio
import logging
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm import usage_summary

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/modules", tags=["独立业务模块"])
Market = Literal["US", "DE", "GB", "JP"]
TOPICS = {
    "policy": "退货 退款 售后政策",
    "shipping": "物流 配送 时效 延误",
    "customs": "税费 关税 清关 合规",
    "reviews": "评论 评价 优缺点",
    "general": "客服 售后",
}


class ModuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")


class ProductRequest(ModuleRequest):
    sku: str = Field(pattern=r"^SKU-\d{1,12}$")
    action: Literal["inventory", "restock"] = "inventory"
    quantity: int | None = Field(default=None, ge=1, le=10000, strict=True)
    target_stock: int | None = Field(default=None, ge=1, le=10000, strict=True)

    @model_validator(mode="after")
    def check_quantity(self):
        values = sum(value is not None for value in (self.quantity, self.target_stock))
        if values != int(self.action == "restock"):
            raise ValueError("补货须选择新增数量或目标库存中的一项")
        return self


class OrderRequest(ModuleRequest):
    order_id: str = Field(pattern=r"^CB-\d{1,12}$")
    action: Literal["quote", "shipment"] = "quote"


class SupportRequest(ModuleRequest):
    sku: str | None = Field(default=None, pattern=r"^SKU-\d{1,12}$")
    topic: Literal["policy", "shipping", "customs", "reviews", "general"] = "general"
    question: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def check_topic(self):
        if self.topic == "reviews" and not self.sku:
            raise ValueError("评论分析需要选定商品")
        return self


class MarketingRequest(ModuleRequest):
    sku: str = Field(pattern=r"^SKU-\d{1,12}$")
    country: Market
    notes: str = Field(default="", max_length=600)


def existing(store, body):
    try:
        if getattr(body, "sku", None):
            store.product(body.sku)
        if getattr(body, "order_id", None):
            store.order(body.order_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


def history_answer(result, proposals, warnings):
    lines = []
    if product := result.get("product"):
        lines.append(f"商品 {product['name']} · 本地库存 {product['stock']} 件")
    if supplier := result.get("supplier"):
        lines.append(f"供应商可用库存 {supplier['available']} 件")
    if order := result.get("order"):
        lines.append(f"订单 {order['order_id']} · {order['status']}")
    if logistics := result.get("logistics"):
        lines.append(f"模拟运费 USD {logistics['cost_usd']:.2f}")
    lines.extend(result[key] for key in ("message", "analysis", "notice") if result.get(key))
    for language, draft in result.get("drafts", {}).items():
        lines.append(f"{language.upper()}：{draft}")
    for source in result.get("sources", []):
        lines.append(f"[{source['source']}] {source['text']}")
    if proposals:
        lines.append("已生成待确认单，尚未执行。")
    return "\n\n".join(lines + warnings)


async def execute(request, body, name, entities, title, *, analyze=False):
    app = request.app
    session_id = body.session_id
    if session_id in app.state.active_sessions:
        raise HTTPException(409, "同一会话已有任务，请稍后再试")
    existing(app.state.store, body)
    app.state.active_sessions.add(session_id)
    request_id = uuid4().hex
    try:
        workflow = app.state.workflow
        state = {
            "message": title,
            "session_id": session_id,
            "request_id": request_id,
            "entities": {
                "quantity": None,
                "target_stock": None,
                "restock": False,
                "ship": False,
                "clarification": "",
                **entities,
            },
            "pending": [name],
            "results": {},
            "proposals": [],
            "history": [],
            "warnings": [],
            "model_calls": [],
        }
        handler = workflow.agent_node(name) if analyze else getattr(workflow, name)
        async with asyncio.timeout(request.app.state.module_timeout):
            outcome = await handler(state)
        result = outcome["results"][name]
        proposals = outcome.get("proposals", [])
        warnings = outcome.get("warnings", [])
        usage = usage_summary(
            outcome.get("model_calls", []),
            configured=analyze and workflow.model.enabled,
            fallback=bool(warnings),
        )
        app.state.store.add_turn(
            request_id,
            session_id,
            title,
            history_answer(result, proposals, warnings),
            {"module": name, "model_usage": usage},
        )
        return {
            "module": name,
            "facts": result,
            "proposals": proposals,
            "warnings": warnings,
            "model_usage": usage,
            "request_id": request_id,
        }
    except TimeoutError as exc:
        raise HTTPException(504, "任务查询或生成超时，请检查后重试。") from exc
    except Exception as exc:
        logger.exception("Module request failed: %s / %s", name, request_id)
        raise HTTPException(503, "当前模块暂时不可用，请稍后重试。") from exc
    finally:
        app.state.active_sessions.discard(session_id)


@router.post("/product")
async def product(body: ProductRequest, request: Request):
    action = "供应商库存查询"
    if body.action == "restock":
        action = f"补货 {body.quantity} 件" if body.quantity else f"目标库存 {body.target_stock} 件"
    return await execute(
        request,
        body,
        "product",
        {
            "sku": body.sku,
            "restock": body.action == "restock",
            "quantity": body.quantity,
            "target_stock": body.target_stock,
        },
        f"商品中心 · {body.sku} · {action}",
    )


@router.post("/order")
async def order(body: OrderRequest, request: Request):
    action = "创建配送确认单" if body.action == "shipment" else "订单与物流报价"
    return await execute(
        request,
        body,
        "order",
        {"order_id": body.order_id, "ship": body.action == "shipment"},
        f"订单中心 · {body.order_id} · {action}",
    )


@router.post("/support")
async def support(body: SupportRequest, request: Request):
    query = f"{TOPICS[body.topic]} {body.question}".strip()
    return await execute(
        request,
        body,
        "support",
        {"sku": body.sku or "", "search_query": query, "support_topic": body.topic},
        f"客服中心 · {body.sku or '通用政策'} · {query}",
        analyze=True,
    )


@router.post("/marketing")
async def marketing(body: MarketingRequest, request: Request):
    product = request.app.state.store
    existing(product, body)
    if body.country not in product.product(body.sku)["markets"]:
        raise HTTPException(422, "商品暂未上架该市场")
    return await execute(
        request,
        body,
        "marketing",
        {"sku": body.sku, "country": body.country},
        f"营销中心 · {body.sku} · {body.country} 市场文案 · {body.notes}",
        analyze=True,
    )
