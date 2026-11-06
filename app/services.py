"""独立 A2A/MCP 服务入口，与单进程模式共用业务实现。"""

import argparse

import uvicorn

from app.config import Settings
from app.external import create_external
from app.knowledge import Knowledge
from app.store import Store
from app.tools import ToolGateway, create_tools


def main():
    parser = argparse.ArgumentParser(description="启动独立供应商、物流或 MCP 服务")
    parser.add_argument("role", choices=["supplier", "logistics", "mcp"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    settings = Settings()
    store = Store(settings.database_path)
    knowledge = Knowledge(settings, store)
    mcp = create_tools(store, knowledge)
    port = args.port or {"supplier": 8001, "logistics": 8002, "mcp": 8003}[args.role]
    try:
        if args.role == "mcp":
            mcp.run(transport="http", host=args.host, port=port)
        else:
            app = create_external(
                args.role, ToolGateway(settings.mcp_url or mcp), f"http://localhost:{port}"
            )
            uvicorn.run(app, host=args.host, port=port)
    finally:
        knowledge.close()


if __name__ == "__main__":
    main()
