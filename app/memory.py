import json

from redis.asyncio import Redis

from app.config import Settings
from app.store import Store


class Memory:
    def __init__(self, settings: Settings, store: Store):
        if settings.memory_backend not in {"sqlite", "redis"}:
            raise ValueError("MEMORY_BACKEND 只支持 sqlite 或 redis")
        self.store = store
        self.redis = (
            Redis.from_url(settings.redis_url) if settings.memory_backend == "redis" else None
        )

    async def load(self, session_id: str) -> dict:
        if self.redis:
            value = await self.redis.get(f"commerce:memory:{session_id}")
            return json.loads(value) if value else {}
        return self.store.load_memory(session_id)

    async def save(self, session_id: str, value: dict):
        if self.redis:
            await self.redis.set(f"commerce:memory:{session_id}", json.dumps(value), ex=604800)
        else:
            self.store.save_memory(session_id, value)

    async def close(self):
        if self.redis:
            await self.redis.aclose()
