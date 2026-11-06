"""A2A 0.3 JSON-RPC，使用官方 SDK 的 Agent Card、Task 和 Artifact。"""

import json
from uuid import uuid4

import httpx
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, DataPart, Part, TaskState
from a2a.utils import new_task

from app.tools import ToolGateway


class CommerceExecutor(AgentExecutor):
    def __init__(self, role: str, tools: ToolGateway):
        self.role = role
        self.tools = tools

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task or new_task(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(TaskState.working)
        try:
            payload = json.loads(context.get_user_input())
            if self.role == "supplier":
                result = await self.tools.call("supplier_inventory", {"sku": payload["sku"]})
                if payload.get("operation") == "restock":
                    result["request"] = {
                        "sku": payload["sku"],
                        "quantity": payload["quantity"],
                        "status": "draft",
                    }
            else:
                result = await self.tools.call(
                    "shipping_quote",
                    {
                        "sku": payload["sku"],
                        "country": payload["country"],
                        "quantity": payload.get("quantity", 1),
                    },
                )
            await updater.add_artifact(
                [Part(root=DataPart(data=result))], name=f"{self.role}-result"
            )
            await updater.complete()
        except Exception:
            # 业务错误以 failed task 返回，不向调用方泄露内部异常信息。
            await updater.update_status(TaskState.failed, final=True)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        if context.task_id and context.context_id:
            await TaskUpdater(event_queue, context.task_id, context.context_id).update_status(
                TaskState.canceled, final=True
            )


def create_external(role: str, tools: ToolGateway, base_url: str):
    card = AgentCard(
        name="供应商 Agent" if role == "supplier" else "物流 Agent",
        description="演示库存与补货草案" if role == "supplier" else "演示配送报价与时效",
        url=base_url.rstrip("/") + "/",
        version="0.1.0",
        protocol_version="0.3.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id=role,
                name=role,
                description="只读查询和业务草案，不执行真实交易",
                tags=["crossborder", role],
                examples=["查询库存或配送报价"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=CommerceExecutor(role, tools), task_store=InMemoryTaskStore()
    )
    return A2AStarletteApplication(agent_card=card, http_handler=handler).build()


class DelegationError(Exception):
    pass


class A2AGateway:
    def __init__(self, embedded: dict, urls: dict):
        self.embedded = embedded
        self.urls = urls

    async def delegate(self, role: str, payload: dict) -> dict:
        # 外部地址只来自配置，不接受用户请求中的 URL，避免任意地址请求。
        url = self.urls.get(role) or f"http://{role}.local"
        transport = None if self.urls.get(role) else httpx.ASGITransport(app=self.embedded[role])
        async with httpx.AsyncClient(transport=transport, timeout=15, trust_env=False) as client:
            card_response = await client.get(url.rstrip("/") + "/.well-known/agent-card.json")
            card_response.raise_for_status()
            card = AgentCard.model_validate(card_response.json())
            # 使用配置的端点发请求，不盲目信任远端 Agent Card 提供的新地址。
            if role not in [skill.id for skill in card.skills]:
                raise DelegationError("外部 Agent 不具备所需技能")
            response = await client.post(
                url.rstrip("/") + "/",
                json={
                    "jsonrpc": "2.0",
                    "id": uuid4().hex,
                    "method": "message/send",
                    "params": {
                        "message": {
                            "messageId": uuid4().hex,
                            "role": "user",
                            "parts": [{"kind": "text", "text": json.dumps(payload)}],
                        }
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            task = data.get("result", {})
            if data.get("error") or task.get("status", {}).get("state") != "completed":
                raise DelegationError("外部协作失败，请检查商品、目的地或服务状态")
            for artifact in task.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("kind") == "data":
                        return {"task_id": task["id"], "agent": card.name, **part["data"]}
            raise DelegationError("外部 Agent 未返回业务结果")
