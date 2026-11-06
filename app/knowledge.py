import hashlib
import math
import re

from app.config import Settings
from app.store import Store


def embed(text: str, dimension: int = 256) -> list[float]:
    """无下载的词/字双元组哈希向量。演示用，不冒充语义模型。"""
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text)
    tokens += [text[i : i + 2] for i in range(max(0, len(text) - 1))]
    vector = [0.0] * dimension
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:4], "big") % dimension] += 1
    norm = math.sqrt(sum(v * v for v in vector)) or 1
    return [v / norm for v in vector]


class Knowledge:
    def __init__(self, settings: Settings, store: Store):
        self.docs = store.all("documents")
        self.client = None
        self.collection = "commerce_knowledge_hash256_v1"
        if settings.knowledge_backend == "milvus":
            from pymilvus import MilvusClient

            self.client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
            if not self.client.has_collection(self.collection):
                self.client.create_collection(
                    collection_name=self.collection,
                    dimension=256,
                    metric_type="COSINE",
                    consistency_level="Strong",
                )
            self.client.upsert(
                collection_name=self.collection,
                data=[
                    {**d, "id": i, "vector": embed(d["text"]), "source_id": d["id"]}
                    for i, d in enumerate(self.docs)
                ],
            )
        elif settings.knowledge_backend != "local":
            raise ValueError("KNOWLEDGE_BACKEND 只支持 local 或 milvus")

    def search(self, query: str, sku: str = "", limit: int = 3) -> list[dict]:
        candidates = [d for d in self.docs if not d.get("sku") or d["sku"] == sku]
        vector = embed(query)
        if self.client:
            # SKU 来自产品目录；文档量小，先取全量候选再按归属过滤，避免拼接表达式。
            hits = self.client.search(
                collection_name=self.collection,
                data=[vector],
                limit=len(self.docs),
                output_fields=["source_id"],
            )[0]
            scores = {h["entity"]["source_id"]: h["distance"] for h in hits}
            ranked = sorted(candidates, key=lambda d: scores.get(d["id"], -1), reverse=True)
        else:
            ranked = sorted(
                candidates,
                key=lambda d: sum(a * b for a, b in zip(vector, embed(d["text"]))),
                reverse=True,
            )
        return [{**d, "source": d["id"]} for d in ranked[:limit]]

    def close(self):
        if self.client:
            self.client.close()
