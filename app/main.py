import asyncio
import json
import logging
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.access import COOKIE_NAME, SESSION_SECONDS, DemoAccess, DemoAuth
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


async def heartbeat_stream(stream):
    iterator = stream.__aiter__()
    pending = asyncio.create_task(anext(iterator))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=10)
            if not done:
                yield None
                continue
            try:
                item = pending.result()
            except StopAsyncIteration:
                break
            yield item
            pending = asyncio.create_task(anext(iterator))
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    password = settings.demo_password.get_secret_value()
    if settings.public_demo and len(password) < 12:
        raise ValueError("在线演示必须设置至少12位的 DEMO_PASSWORD")
    if password and not settings.demo_username.strip():
        raise ValueError("DEMO_USERNAME 不能为空")

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

    auth = DemoAuth(settings.demo_username, password)
    app = FastAPI(title="跨境电商混合智能中台", version="0.1.0", lifespan=lifespan)
    app.state.auth = auth
    app.add_middleware(DemoAccess, auth=auth)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def login_page(username: str, error: str = "", status: int = 200):
        template = (STATIC / "login.html").read_text(encoding="utf-8")
        template = template.replace("{{username}}", escape(username, quote=True))
        template = template.replace("{{error}}", escape(error))
        return HTMLResponse(template, status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/login", include_in_schema=False)
    async def login_form(request: Request):
        if not auth.enabled or auth.valid(request.cookies.get(COOKIE_NAME, "")):
            return RedirectResponse("/", status_code=303)
        return login_page(settings.demo_username)

    @app.post("/login", include_in_schema=False)
    async def login(
        request: Request,
        username: Annotated[str, Form()] = "",
        password: Annotated[str, Form()] = "",
    ):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        if not username or not password:
            return login_page(username[:80], "请输入用户名和访问密码。", 400)
        if len(username) > 80 or len(password) > 1024 or not auth.authenticate(username, password):
            return login_page(username[:80], "用户名或密码不正确，请检查后重试。", 401)
        # 每次登录换一个会话，避免沿用旧令牌；密码不会进入 Cookie。
        auth.revoke(request.cookies.get(COOKIE_NAME, ""))
        response = RedirectResponse("/", status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE_NAME,
            auth.issue(),
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=settings.public_demo or request.url.scheme == "https",
            samesite="lax",
            path="/",
        )
        return response

    @app.post("/logout", include_in_schema=False)
    async def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE_NAME, ""))
        response = RedirectResponse(
            "/login" if auth.enabled else "/",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
        response.delete_cookie(
            COOKIE_NAME,
            path="/",
            httponly=True,
            secure=settings.public_demo or request.url.scheme == "https",
            samesite="lax",
        )
        return response

    @app.get("/api/auth/me", include_in_schema=False)
    async def current_user():
        return {"login_required": auth.enabled, "username": settings.demo_username}

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "business_data": "synthetic-demo",
            "temporary_data": settings.demo_temporary_data,
            "model": "configured" if app.state.workflow.model.enabled else "rule-demo",
            "model_name": app.state.workflow.model.name
            if app.state.workflow.model.enabled
            else None,
            "model_status": app.state.workflow.model.last_status,
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

    @app.get("/api/model/status")
    async def model_status():
        return app.state.workflow.model.status()

    @app.get("/api/history/{session_id}")
    async def history(session_id: str):
        return app.state.store.history(session_id)

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
                async with asyncio.timeout(settings.workflow_timeout_seconds):
                    stream = app.state.workflow.graph.astream(
                        state, stream_mode=["updates", "custom"], version="v2"
                    )
                    async for part in heartbeat_stream(stream):
                        if part is None:
                            yield sse("heartbeat", {"request_id": request_id})
                            continue
                        if part["type"] == "custom":
                            event = part["data"]
                            yield sse(event["kind"], event)
                            continue
                        for node, patch in part["data"].items():
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
                            "model_usage": state.get("model_usage", {}),
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
