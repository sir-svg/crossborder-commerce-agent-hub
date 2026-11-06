from fastmcp import Client, FastMCP

from app.knowledge import Knowledge
from app.store import Store


def create_tools(store: Store, knowledge: Knowledge) -> FastMCP:
    server = FastMCP("Crossborder Commerce Tools")

    @server.tool
    def search_products(query: str = "") -> list[dict]:
        """查询演示商品目录，返回美元价格、支持市场和本地库存。"""
        return [p for p in store.all("products") if not query or query.lower() in str(p).lower()]

    @server.tool
    def get_order(order_id: str) -> dict:
        """根据订单号读取演示订单，不会修改订单。"""
        return store.order(order_id)

    @server.tool
    def list_orders(country: str = "", status: str = "") -> list[dict]:
        """按目的市场和订单状态列出演示订单，不创建配送。"""
        return [
            order
            for order in store.all("orders")
            if (not country or order["country"] == country)
            and (not status or order["status"] == status)
        ]

    @server.tool
    def search_knowledge(query: str, sku: str = "") -> list[dict]:
        """检索售后政策、评论和营销素材，保留可追溯的来源编号。"""
        return knowledge.search(query, sku)

    @server.tool
    def supplier_inventory(sku: str) -> dict:
        """供应商演示接口：库存和补货交期，只读。"""
        p = store.product(sku)
        return {
            "sku": sku,
            "available": p["supplier_stock"],
            "lead_days": 4,
            "warehouse": "深圳供应商演示仓",
            "demo": True,
        }

    @server.tool
    def shipping_quote(sku: str, country: str, quantity: int = 1) -> dict:
        """物流演示接口：按目的地和重量估算美元运费，不含税费。"""
        routes = {"US": (5, 8, 8.5), "DE": (7, 10, 10.0), "GB": (6, 9, 9.0), "JP": (3, 5, 5.5)}
        if country not in routes or not 1 <= quantity <= 10000:
            raise ValueError("不支持的目的地或数量")
        p = store.product(sku)
        if country not in p["markets"]:
            raise ValueError("商品暂未上架该市场")
        low, high, base = routes[country]
        price = round(base + p["weight_kg"] * quantity * 2.5, 2)
        return {
            "country": country,
            "cost_usd": price,
            "eta_days": [low, high],
            "channel": "演示带电专线" if sku == "SKU-1001" else "演示标准专线",
            "tax_included": False,
            "demo": True,
        }

    return server


class ToolGateway:
    def __init__(self, source):
        self.source = source

    async def call(self, name: str, arguments: dict):
        # 同一接口可切换进程内 MCP 或远端 Streamable HTTP MCP 服务。
        async with Client(self.source) as client:
            result = await client.call_tool(name, arguments)
            # FastMCP 可能把 data 解码成动态 Pydantic 模型；业务边界使用标准 JSON。
            data = result.structured_content
            if data is not None:
                return data["result"] if set(data) == {"result"} else data
            from fastapi.encoders import jsonable_encoder

            return jsonable_encoder(result.data)
