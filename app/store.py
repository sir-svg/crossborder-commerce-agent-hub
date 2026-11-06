import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class Store:
    """每次操作独立连接；确认单在同一事务中检查、执行、记录。"""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memory (
                    session_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL, result TEXT);
            """)
            seed = json.loads(Path(__file__).with_name("seed.json").read_text(encoding="utf-8"))
            for table, key in [("products", "sku"), ("orders", "order_id"), ("documents", "id")]:
                for item in seed[table]:
                    db.execute(
                        f"INSERT OR IGNORE INTO {table} VALUES (?, ?)",
                        (item[key], json.dumps(item, ensure_ascii=False)),
                    )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self, table: str) -> list[dict]:
        if table not in {"products", "orders", "documents"}:
            raise ValueError("unknown collection")
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(f"SELECT payload FROM {table}")]

    def product(self, sku: str) -> dict:
        for p in self.all("products"):
            if p["sku"] == sku:
                return p
        raise ValueError(f"商品 {sku} 不存在")

    def order(self, order_id: str) -> dict:
        for o in self.all("orders"):
            if o["order_id"] == order_id:
                return o
        raise ValueError(f"订单 {order_id} 不存在")

    def load_memory(self, session_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM memory WHERE session_id=?", (session_id,)
            ).fetchone()
            return json.loads(row[0]) if row else {}

    def save_memory(self, session_id: str, value: dict):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO memory VALUES (?, ?)",
                (session_id, json.dumps(value, ensure_ascii=False)),
            )

    def propose(self, session_id: str, kind: str, payload: dict) -> dict:
        proposal_id = uuid4().hex
        created = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            # 同一会话的相同待确认任务复用，不重复生成单据。
            for row in db.execute(
                "SELECT * FROM proposals WHERE session_id=? AND kind=? AND status='pending'",
                (session_id, kind),
            ):
                if json.loads(row["payload"]) == payload:
                    return self._proposal(row)
            db.execute(
                "INSERT INTO proposals (id,session_id,kind,payload,created_at) VALUES (?,?,?,?,?)",
                (proposal_id, session_id, kind, json.dumps(payload, ensure_ascii=False), created),
            )
        return {
            "id": proposal_id,
            "kind": kind,
            "payload": payload,
            "status": "pending",
            "created_at": created,
            "result": None,
        }

    @staticmethod
    def _proposal(row) -> dict:
        value = dict(row)
        value.pop("session_id", None)
        value["payload"] = json.loads(value["payload"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def proposals(self, session_id: str) -> list[dict]:
        with self.connect() as db:
            return [
                self._proposal(row)
                for row in db.execute(
                    "SELECT * FROM proposals WHERE session_id=? ORDER BY created_at DESC LIMIT 30",
                    (session_id,),
                )
            ]

    def approve(self, session_id: str, proposal_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM proposals WHERE id=? AND session_id=?", (proposal_id, session_id)
            ).fetchone()
            if not row:
                raise KeyError("确认单不存在")
            if row["status"] == "approved":
                return self._proposal(row)
            payload = json.loads(row["payload"])
            if row["kind"] == "restock":
                p = json.loads(
                    db.execute(
                        "SELECT payload FROM products WHERE sku=?", (payload["sku"],)
                    ).fetchone()[0]
                )
                qty = payload["quantity"]
                if qty > p["supplier_stock"]:
                    raise ValueError("供应商可用库存不足，请重新查询")
                p["stock"] += qty
                p["supplier_stock"] -= qty
                db.execute(
                    "UPDATE products SET payload=? WHERE sku=?",
                    (json.dumps(p, ensure_ascii=False), p["sku"]),
                )
                result = {"message": "已模拟补货入库", "stock": p["stock"], "demo": True}
            elif row["kind"] == "shipment":
                order_id = payload["order_id"]
                o = json.loads(
                    db.execute(
                        "SELECT payload FROM orders WHERE order_id=?", (order_id,)
                    ).fetchone()[0]
                )
                if o["status"] != "paid":
                    raise ValueError("订单已发货或状态不允许配送")
                p = json.loads(
                    db.execute("SELECT payload FROM products WHERE sku=?", (o["sku"],)).fetchone()[
                        0
                    ]
                )
                if p["stock"] < o["quantity"]:
                    raise ValueError("本地库存不足，请先补货")
                p["stock"] -= o["quantity"]
                o.update(status="in_transit", tracking=f"DEMO-{proposal_id[:10].upper()}")
                db.execute(
                    "UPDATE products SET payload=? WHERE sku=?",
                    (json.dumps(p, ensure_ascii=False), p["sku"]),
                )
                db.execute(
                    "UPDATE orders SET payload=? WHERE order_id=?",
                    (json.dumps(o, ensure_ascii=False), order_id),
                )
                result = {"message": "已模拟创建配送", "tracking": o["tracking"], "demo": True}
            else:
                raise ValueError("未知确认单类型")
            db.execute(
                "UPDATE proposals SET status='approved', result=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), proposal_id),
            )
            return self._proposal(
                db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            )
