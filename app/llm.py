import json

import httpx

from app.config import Settings


class Model:
    """可选的兼容 Chat Completions 模型服务；默认不调用网络模型。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.llm_base_url and settings.llm_model)

    async def complete(self, instruction: str, data: dict) -> str:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            response = await client.post(
                self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json={
                    "model": self.settings.llm_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                    ],
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("模型返回为空")
            return content
