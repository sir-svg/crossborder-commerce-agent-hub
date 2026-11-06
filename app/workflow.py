import logging
from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.analysis import PROMPTS, SUMMARY_PROMPT, Analysis, validate_analysis, validate_sources
from app.external import A2AGateway
from app.llm import Model, ModelError, usage_summary
from app.memory import Memory
from app.planning import PLANNING_PROMPT, Plan, apply_plan, rules
from app.store import Store
from app.tools import ToolGateway

logger = logging.getLogger(__name__)


class State(TypedDict, total=False):
    message: str
    session_id: str
    request_id: str
    entities: dict
    pending: list[str]
    results: dict
    proposals: list[dict]
    warnings: list[str]
    answer: str
    history: list[dict]
    model_calls: list[dict]
    model_unavailable: bool
    model_usage: dict


class Workflow:
    def __init__(
        self, store: Store, tools: ToolGateway, external: A2AGateway, memory: Memory, model: Model
    ):
        self.store, self.tools, self.external = store, tools, external
        self.memory, self.model = memory, model
        graph = StateGraph(State)
        graph.add_node("orchestrator", self.orchestrator)
        graph.add_node("dispatch", lambda state: {})
        for name in ["product", "order", "support", "marketing"]:
            graph.add_node(name, self.agent_node(name))
            graph.add_edge(name, "dispatch")
        graph.add_node("summarize", self.summarize)
        graph.add_edge(START, "orchestrator")
        graph.add_edge("orchestrator", "dispatch")
        graph.add_conditional_edges(
            "dispatch", lambda s: s["pending"][0] if s["pending"] else "summarize"
        )
        graph.add_edge("summarize", END)
        # 长期会话实体由 SQLite/Redis memory 保存；单轮图状态仅在这次执行中共享。
        self.graph = graph.compile()

    @staticmethod
    def emit(event: dict):
        try:
            get_stream_writer()(event)
        except RuntimeError:
            pass

    def agent_node(self, name):
        async def run(state):
            self.emit({"kind": "activity", "node": name, "phase": "running"})
            patch = await getattr(self, name)(state)
            calls = list(state.get("model_calls", []))
            warnings = list(state.get("warnings", []))
            result = patch["results"][name]
            unavailable = state.get("model_unavailable", False)
            if self.model.enabled and not unavailable:
                try:
                    analysis = await self.model.structured(
                        PROMPTS[name],
                        {
                            "question": state["message"],
                            "entities": patch.get("entities", state["entities"]),
                            "facts": result,
                            "allowed_citations": [
                                doc["source"] for doc in result.get("sources", [])
                            ],
                            "recent_dialogue": state.get("history", [])[-4:],
                        },
                        Analysis,
                        usage=calls,
                        purpose=name,
                        max_tokens=1600,
                    )
                    validate_analysis(analysis, result, name)
                    result["analysis"] = analysis.summary
                    result["citations"] = analysis.citations
                    if name == "marketing" and result.get("drafts"):
                        result["drafts"] = analysis.drafts
                    result["model_generated"] = True
                except (ModelError, ValueError) as exc:
                    code = getattr(exc, "code", "invalid_reply")
                    logger.warning("Model analysis failed: %s / %s", name, code)
                    warnings.append(f"{name}助手的模型分析暂不可用，已保留工具查询结果。")
                    unavailable = code != "invalid_reply"
            patch.update(model_calls=calls, warnings=warnings, model_unavailable=unavailable)
            return patch

        return run

    async def orchestrator(self, state: State):
        self.emit({"kind": "activity", "node": "orchestrator", "phase": "running"})
        memory = await self.memory.load(state["session_id"])
        entities, agents = rules(state["message"], memory, self.store.all("products"))
        history = memory.get("_history", [])[-6:]
        calls, warnings = [], []
        unavailable = False
        if self.model.enabled:
            try:
                plan = await self.model.structured(
                    PLANNING_PROMPT,
                    {
                        "question": state["message"],
                        "remembered_entities": {
                            key: memory[key]
                            for key in ("sku", "country", "order_id")
                            if key in memory
                        },
                        "recent_dialogue": history,
                        "catalog": [
                            {key: p[key] for key in ("sku", "name", "keywords", "markets")}
                            for p in self.store.all("products")
                        ],
                        "orders": [
                            {key: o[key] for key in ("order_id", "sku", "country", "status")}
                            for o in self.store.all("orders")
                        ],
                    },
                    Plan,
                    usage=calls,
                    purpose="planning",
                    max_tokens=900,
                )
                entities, agents = apply_plan(plan, state["message"], memory, entities)
            except (ModelError, ValueError) as exc:
                code = getattr(exc, "code", "invalid_reply")
                logger.warning("Model planning failed: %s", code)
                warnings.append("模型规划暂不可用，已改用规则查询；结果中会注明降级情况。")
                unavailable = code != "invalid_reply"
        if "order" in agents:
            agents = ["order"] + [name for name in agents if name != "order"]
        return {
            "entities": entities,
            "pending": agents,
            "results": {},
            "proposals": [],
            "warnings": warnings,
            "answer": "",
            "history": history,
            "model_calls": calls,
            "model_unavailable": unavailable,
        }

    @staticmethod
    def finished(state, name, result, **extra):
        return {
            "pending": state["pending"][1:],
            "results": {**state["results"], name: result},
            **extra,
        }

    async def product(self, state: State):
        e = state["entities"]
        if not e.get("sku"):
            products = await self.tools.call("search_products", {})
            return self.finished(
                state,
                "product",
                {"catalog": products, "message": "请指定商品名称或 SKU，例如 SKU-1001。"},
            )
        try:
            product = self.store.product(e["sku"])
        except ValueError as exc:
            return self.finished(state, "product", {"message": str(exc)})
        # 商品也经标准 MCP 工具读取，而不是把外部平台逻辑放进 Agent。
        products = await self.tools.call("search_products", {"query": product["sku"]})
        result = {"product": products[0]}
        proposals = list(state["proposals"])
        try:
            supplier = await self.external.delegate(
                "supplier",
                {
                    "sku": product["sku"],
                    "quantity": e["quantity"],
                    "operation": "restock" if e["restock"] else "inventory",
                },
            )
            result["supplier"] = supplier
            if e["restock"]:
                qty = e["quantity"]
                if e.get("target_stock") is not None:
                    qty = max(0, e["target_stock"] - product["stock"])
                if e.get("clarification"):
                    result["notice"] = e["clarification"]
                elif qty is None:
                    result["notice"] = "请明确需要补货多少件，或者希望库存补到多少件。"
                elif e.get("target_stock") and qty == 0:
                    result["notice"] = "当前库存已经达到目标，无需补货。"
                elif not 1 <= qty <= 10000:
                    result["notice"] = "补货数量必须为1至10000件。"
                elif qty > supplier["available"]:
                    result["notice"] = "补货数量超过供应商可用库存，未生成确认单。"
                else:
                    proposals.append(
                        self.store.propose(
                            state["session_id"], "restock", {"sku": product["sku"], "quantity": qty}
                        )
                    )
        except Exception:
            logger.exception("Supplier delegation failed")
            result["notice"] = "供应商暂时不可用，本地商品信息仍可查看；未生成补货确认单。"
        return self.finished(state, "product", result, proposals=proposals)

    async def order(self, state: State):
        e = dict(state["entities"])
        if not e.get("order_id"):
            candidates = await self.tools.call(
                "list_orders",
                {
                    "country": e.get("country", "")
                    if any(
                        value in state["message"]
                        for value in ["美国", "德国", "英国", "日本", "US", "DE", "GB", "JP"]
                    )
                    else "",
                    "status": "paid" if e["ship"] else "",
                },
            )
            if e["ship"] and len(candidates) == 1:
                e["order_id"] = candidates[0]["order_id"]
            else:
                return self.finished(
                    state,
                    "order",
                    {
                        "orders": candidates,
                        "message": "请确认需要处理的订单号。"
                        if e["ship"]
                        else "以下是查询到的演示订单。",
                    },
                )
        try:
            self.store.order(e["order_id"])
        except ValueError as exc:
            return self.finished(state, "order", {"message": str(exc)})
        order = await self.tools.call("get_order", {"order_id": e["order_id"]})
        e.update(sku=order["sku"], country=order["country"])
        result = {"order": order}
        proposals = list(state["proposals"])
        try:
            result["logistics"] = await self.external.delegate(
                "logistics",
                {
                    "sku": order["sku"],
                    "country": order["country"],
                    "quantity": order["quantity"],
                },
            )
            if e["ship"]:
                if e.get("clarification"):
                    result["notice"] = e["clarification"]
                elif order["status"] == "paid":
                    proposals.append(
                        self.store.propose(
                            state["session_id"], "shipment", {"order_id": order["order_id"]}
                        )
                    )
                else:
                    result["notice"] = "该订单已配送，不能重复发货。"
        except Exception:
            logger.exception("Logistics delegation failed")
            result["notice"] = "物流服务暂不可用，未生成配送确认单。"
        return self.finished(state, "order", result, entities=e, proposals=proposals)

    async def support(self, state: State):
        docs = await self.tools.call(
            "search_knowledge",
            {
                "query": state["entities"].get("search_query", state["message"]),
                "sku": state["entities"].get("sku", ""),
            },
        )
        return self.finished(
            state, "support", {"sources": docs, "message": "以下为演示政策与评论，退款需人工审核。"}
        )

    async def marketing(self, state: State):
        sku = state["entities"].get("sku")
        if not sku:
            return self.finished(state, "marketing", {"message": "请指定要推广的商品。"})
        try:
            p = self.store.product(sku)
        except ValueError as exc:
            return self.finished(state, "marketing", {"message": str(exc)})
        docs = await self.tools.call(
            "search_knowledge", {"query": "营销 卖点 评论 " + p["name"], "sku": sku}
        )
        country = state["entities"]["country"]
        if country not in p["markets"]:
            return self.finished(state, "marketing", {"message": "商品暂未上架该市场。"})
        copy = {
            "zh": f"发现 {p['name']}：{p['description']} 参考价 ${p['price_usd']:.2f}。",
            "en": f"Meet {p['name'].split()[0]}. Explore your everyday essentials. "
            f"From USD {p['price_usd']:.2f}. Taxes and shipping extra.",
        }
        if country == "DE":
            copy["de"] = (
                f"Entdecke {p['name'].split()[0]}. Ab {p['price_usd']:.2f} USD. "
                "Steuern und Versand nicht enthalten."
            )
        if country == "JP":
            copy["ja"] = (
                f"{p['name'].split()[0]}で毎日をもっと快適に。"
                f"参考価格 {p['price_usd']:.2f} USD。税金・送料別。"
            )
        return self.finished(
            state,
            "marketing",
            {
                "market": country,
                "drafts": copy,
                "sources": docs,
                "message": "营销草稿，发布前请审核；未自动投放。",
            },
        )

    async def summarize(self, state: State):
        lines = []
        for name, result in state["results"].items():
            if result.get("analysis"):
                lines.append(result["analysis"])
            elif result.get("message"):
                lines.append(result["message"])
            if name == "product" and "product" in result:
                p = result["product"]
                lines.append(
                    f"{p['name']}（{p['sku']}）：${p['price_usd']:.2f}，本地库存{p['stock']}件。"
                )
                if "supplier" in result:
                    lines.append(
                        f"供应商可用库存{result['supplier']['available']}件，演示补货交期4天。"
                    )
            if name == "order" and "order" in result:
                o = result["order"]
                lines.append(
                    f"订单 {o['order_id']}：{o['status']}，目的地 {o['country']}，"
                    f"运单 {o['tracking'] or '尚未生成'}。"
                )
                if "logistics" in result:
                    quote = result["logistics"]
                    lines.append(
                        f"演示运费 ${quote['cost_usd']:.2f}，"
                        f"预计{quote['eta_days'][0]}至{quote['eta_days'][1]}天，不含税费。"
                    )
            for doc in result.get("sources", []) if name == "support" else []:
                lines.append(f"[{doc['source']}] {doc['text']}")
            if name == "marketing":
                for language, draft in result.get("drafts", {}).items():
                    lines.append(f"{language.upper()}：{draft}")
            if result.get("notice"):
                lines.append(result["notice"])
        if state["proposals"]:
            lines.append("已生成待确认单。请在页面确认后执行本地演示操作。")
        lines += state["warnings"]
        answer = "\n\n".join(lines)
        calls = list(state.get("model_calls", []))
        warnings = list(state.get("warnings", []))
        unavailable = state.get("model_unavailable", False)
        self.emit({"kind": "activity", "node": "summarize", "phase": "running"})
        if self.model.enabled and not unavailable:
            try:
                parts = []
                async for text in self.model.stream(
                    SUMMARY_PROMPT,
                    {
                        "question": state["message"],
                        "facts": state["results"],
                        "proposals": state["proposals"],
                        "recent_dialogue": state.get("history", [])[-4:],
                    },
                    usage=calls,
                    purpose="summary",
                    max_tokens=2200,
                ):
                    parts.append(text)
                    self.emit({"kind": "token", "text": text})
                generated = "".join(parts)
                validate_sources(
                    generated,
                    {
                        doc["source"]
                        for result in state["results"].values()
                        for doc in result.get("sources", [])
                    },
                )
                answer = generated
            except ModelError as exc:
                logger.warning("Model synthesis failed: %s", exc.code)
                warnings.append("模型汇总暂不可用，已展示各助手结果和工具事实。")
                self.emit({"kind": "answer_reset"})
        if state["proposals"]:
            answer += (
                "\n\n【操作状态】本轮生成的确认单尚未执行。请点击确认单后再进行演示补货或配送。"
            )
        if state["entities"].get("clarification"):
            answer += "\n\n【需要补充】" + state["entities"]["clarification"]
        if warnings:
            answer += "\n\n【运行提示】" + "；".join(dict.fromkeys(warnings))
        usage = usage_summary(calls, configured=self.model.enabled, fallback=bool(warnings))
        history = state.get("history", []) + [
            {"question": state["message"], "answer": answer[:1800]}
        ]
        memory = {
            key: value
            for key, value in state["entities"].items()
            if key in {"sku", "country", "order_id"}
        }
        memory["_history"] = history[-6:]
        await self.memory.save(state["session_id"], memory)
        self.store.add_turn(
            state["request_id"],
            state["session_id"],
            state["message"],
            answer,
            {"model_usage": usage},
        )
        return {"answer": answer, "model_calls": calls, "model_usage": usage, "warnings": warnings}
