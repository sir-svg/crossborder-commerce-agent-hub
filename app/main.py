import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.config import Settings
from app.external import A2AGateway, create_external
from app.knowledge import Knowledge
from app.llm import Model
from app.memory import Memory
from app.store import Store
from app.tools import ToolGateway, create_tools
from app.workflow import Workflow

logger = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")


class ApprovalRequest(BaseModel):
    session_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,80}$")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.database_path)
        knowledge = Knowledge(settings, store)
        memory = Memory(settings, store)
        mcp = create_tools(store, knowledge)
        tools = ToolGateway(settings.mcp_url or mcp)
        embedded = {
            role: create_external(role, tools, f"http://{role}.local")
            for role in ["supplier", "logistics"]
        }
        external = A2AGateway(
            embedded, {"supplier": settings.supplier_url, "logistics": settings.logistics_url}
        )
        app.state.store = store
        app.state.memory = memory
        app.state.workflow = Workflow(store, tools, external, memory, Model(settings))
        app.state.active_sessions = set()
        # 通过代理入口公开同一实例的 Card 和 JSON-RPC，避免启动多个任务仓库。
        app.state.embedded = embedded
        try:
            yield
        finally:
            await memory.close()
            knowledge.close()

    app = FastAPI(title="跨境电商混合智能中台", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "business_data": "synthetic-demo",
            "model": "configured" if Model(settings).enabled else "rule-demo",
            "memory": settings.memory_backend,
            "knowledge": settings.knowledge_backend,
            "supplier": "remote" if settings.supplier_url else "embedded-a2a",
            "logistics": "remote" if settings.logistics_url else "embedded-a2a",
            "tools": "remote-mcp" if settings.mcp_url else "in-process-mcp",
        }

    @app.get("/api/dashboard")
    async def dashboard():
        return {
            "products": app.state.store.all("products"),
            "orders": app.state.store.all("orders"),
        }

    @app.get("/api/proposals/{session_id}")
    async def proposals(session_id: str):
        return app.state.store.proposals(session_id)

    @app.post("/api/proposals/{proposal_id}/approve")
    async def approve(proposal_id: str, body: ApprovalRequest):
        if body.session_id in app.state.active_sessions:
            raise HTTPException(409, "请等待当前任务完成后确认")
        try:
            return await asyncio.to_thread(app.state.store.approve, body.session_id, proposal_id)
        except KeyError as exc:
            raise HTTPException(404, "确认单不存在") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/chat/stream")
    async def chat(body: ChatRequest):
        if not body.message.strip():
            raise HTTPException(422, "请输入要处理的任务")
        if body.session_id in app.state.active_sessions:
            raise HTTPException(409, "同一会话已有任务，请稍后再试")
        app.state.active_sessions.add(body.session_id)

        async def events():
            request_id = uuid4().hex
            # 保留到整个流关闭；不自动重试，避免重复生成业务操作。
            try:
                yield sse("start", {"request_id": request_id})
                state = {
                    "message": body.message,
                    "session_id": body.session_id,
                    "request_id": request_id,
                }
                async with asyncio.timeout(120):
                    async for update in app.state.workflow.graph.astream(
                        state, stream_mode="updates"
                    ):
                        for node, patch in update.items():
                            if patch:
                                state.update(patch)
                            if node != "dispatch":
                                yield sse("step", {"node": node, "data": patch})
                    yield sse(
                        "result",
                        {
                            "answer": state["answer"],
                            "facts": state["results"],
                            "proposals": state["proposals"],
                            "request_id": request_id,
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Workflow failed: request_id=%s", request_id)
                yield sse(
                    "error",
                    {"message": "本次任务未完成，请检查服务配置后重试。", "request_id": request_id},
                )
            finally:
                app.state.active_sessions.discard(body.session_id)
            yield sse("done", {"request_id": request_id})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(app.state.active_sessions.discard, body.session_id),
        )

    @app.api_route("/agents/{role}/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def agent_proxy(role: str, path: str, request: Request):
        if role not in app.state.embedded:
            raise HTTPException(404, "Agent 不存在")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app.state.embedded[role]),
            base_url=f"http://{role}.local",
        ) as client:
            response = await client.request(
                request.method,
                "/" + path,
                content=await request.body(),
                headers={"content-type": "application/json"},
            )
        if path == ".well-known/agent-card.json" and response.status_code == 200:
            card = response.json()
            card["url"] = str(request.base_url).rstrip("/") + f"/agents/{role}/"
            return Response(json.dumps(card), media_type="application/json")
        return Response(
            response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
        )

    return app


app = create_app()
