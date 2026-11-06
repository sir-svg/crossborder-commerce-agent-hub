"""阿里云兼容模型客户端：限时、限长、结构校验、有限重试与用量记录。"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncIterator, TypeVar

import httpx
from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)
DEFAULT_BASE = "https://ws-wx3zg6zl1sf52rk4.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash-1017"


class ModelError(Exception):
    def __init__(self, code: str):
        self.code = code
        messages = {
            "not_configured": "尚未配置模型密钥",
            "auth": "模型密钥或业务空间权限不正确",
            "rate_limit": "模型请求较多，请稍后再试",
            "timeout": "模型响应超时",
            "unavailable": "模型服务暂时不可用",
            "invalid_reply": "模型回复格式未通过检查",
        }
        super().__init__(messages.get(code, messages["unavailable"]))


class Model:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.base_url = (settings.llm_base_url.strip() or DEFAULT_BASE).rstrip("/")
        self.name = settings.llm_model.strip() or DEFAULT_MODEL
        self.key = settings.llm_api_key.get_secret_value()
        self.enabled = bool(self.key)
        self.transport = transport
        self.last_status = "unverified" if self.enabled else "disabled"
        self.last_success_at = None
        self.last_error = None

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.name if self.enabled else None,
            "status": self.last_status,
            "last_success_at": self.last_success_at,
            "error": self.last_error,
        }

    def payload(self, instruction, data, *, stream, json_mode=False, max_tokens=1500):
        payload = {
            "model": self.name,
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "stream": stream,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
        }
        if "maas.aliyuncs.com" in self.base_url and self.name.startswith("deepseek"):
            payload["enable_thinking"] = False
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=10),
            transport=self.transport,
        )

    @staticmethod
    def check_status(status: int):
        if status in {401, 403}:
            raise ModelError("auth")
        if status == 429:
            raise ModelError("rate_limit")
        if status >= 400:
            raise ModelError("unavailable")

    def success(self, response, started, purpose, usage):
        self.last_status = "ready"
        self.last_error = None
        self.last_success_at = datetime.now(timezone.utc).isoformat()
        if usage is not None:
            provider_usage = response.get("usage") or {}
            usage.append(
                {
                    "purpose": purpose,
                    "model": response.get("model", self.name),
                    "input_tokens": provider_usage.get("prompt_tokens"),
                    "output_tokens": provider_usage.get("completion_tokens"),
                    "total_tokens": provider_usage.get("total_tokens"),
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )

    def failure(self, code):
        self.last_status = "error"
        self.last_error = code
        return ModelError(code)

    async def complete(
        self,
        instruction: str,
        data: dict,
        *,
        usage=None,
        purpose="answer",
        json_mode=False,
        max_tokens=1500,
    ) -> str:
        if not self.enabled:
            raise ModelError("not_configured")
        started = time.monotonic()
        try:
            async with self.client() as client:
                for attempt in range(2):
                    response = await client.post(
                        self.base_url + "/chat/completions",
                        headers={"Authorization": "Bearer " + self.key},
                        json=self.payload(
                            instruction,
                            data,
                            stream=False,
                            json_mode=json_mode,
                            max_tokens=max_tokens,
                        ),
                    )
                    if (
                        response.status_code == 429 or response.status_code >= 500
                    ) and attempt == 0:
                        await asyncio.sleep(0.5)
                        continue
                    self.check_status(response.status_code)
                    result = response.json()
                    content = result["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or not content.strip():
                        raise ModelError("invalid_reply")
                    self.success(result, started, purpose, usage)
                    return content
        except ModelError as exc:
            raise self.failure(exc.code) from None
        except httpx.TimeoutException:
            raise self.failure("timeout") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self.failure("invalid_reply") from None
        except httpx.HTTPError:
            raise self.failure("unavailable") from None
        raise self.failure("unavailable")

    async def structured(
        self,
        instruction: str,
        data: dict,
        schema: type[T],
        *,
        usage=None,
        purpose="analysis",
        max_tokens=1500,
    ) -> T:
        json_schema = schema.model_json_schema()
        # 把本次可用来源写进结构约束，减少模型把商品编号误当成引用。
        if "allowed_citations" in data and "citations" in json_schema.get("properties", {}):
            citation_schema = json_schema["properties"]["citations"]
            allowed = data["allowed_citations"]
            if allowed:
                citation_schema["items"] = {"type": "string", "enum": allowed}
            else:
                citation_schema["maxItems"] = 0
        prompt = (
            instruction
            + "\n只返回符合此JSON Schema的JSON对象，不使用代码块：\n"
            + json.dumps(json_schema, ensure_ascii=False)
        )
        raw = await self.complete(
            prompt, data, usage=usage, purpose=purpose, json_mode=True, max_tokens=max_tokens
        )
        try:
            if raw.lstrip().startswith("```"):
                raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            return schema.model_validate(json.loads(raw))
        except ValueError:
            raise self.failure("invalid_reply") from None

    async def stream(
        self, instruction: str, data: dict, *, usage=None, purpose="summary", max_tokens=1800
    ) -> AsyncIterator[str]:
        if not self.enabled:
            raise ModelError("not_configured")
        started = time.monotonic()
        result = {"model": self.name}
        received = False
        try:
            async with self.client() as client:
                async with client.stream(
                    "POST",
                    self.base_url + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.key},
                    json=self.payload(instruction, data, stream=True, max_tokens=max_tokens),
                ) as response:
                    self.check_status(response.status_code)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        event = json.loads(chunk)
                        if event.get("error"):
                            raise ModelError("unavailable")
                        result.update({k: event[k] for k in ("model", "usage") if event.get(k)})
                        choices = event.get("choices") or []
                        if choices and choices[0].get("finish_reason") == "length":
                            raise ModelError("invalid_reply")
                        text = choices[0].get("delta", {}).get("content") if choices else None
                        # 不向页面输出模型的 reasoning_content。
                        if text:
                            if not isinstance(text, str):
                                raise ModelError("invalid_reply")
                            received = True
                            yield text
            if not received:
                raise ModelError("invalid_reply")
            self.success(result, started, purpose, usage)
        except ModelError as exc:
            raise self.failure(exc.code) from None
        except httpx.TimeoutException:
            raise self.failure("timeout") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self.failure("invalid_reply") from None
        except httpx.HTTPError:
            raise self.failure("unavailable") from None


def usage_summary(calls: list[dict], *, configured: bool, fallback: bool) -> dict:
    return {
        "mode": "model" if calls else "fallback" if configured else "rules",
        "degraded": fallback,
        "calls": len(calls),
        "model": calls[0]["model"] if calls else None,
        "total_tokens": sum(c["total_tokens"] for c in calls)
        if calls and all(isinstance(c.get("total_tokens"), int) for c in calls)
        else None,
        "details": calls,
    }
