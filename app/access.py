"""在线演示的统一访问口令；使用纯 ASGI 中间件，保留 SSE 流式行为。"""

import base64
import binascii
import hmac

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class DemoAccess:
    def __init__(self, app: ASGIApp, username: str, password: str):
        self.app = app
        self.username = username.encode("utf-8")
        self.password = password.encode("utf-8")

    def authorized(self, scope: Scope) -> bool:
        header = Headers(scope=scope).get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
            username, separator, password = decoded.partition(":")
            valid_user = hmac.compare_digest(username.encode("utf-8"), self.username)
            valid_password = hmac.compare_digest(password.encode("utf-8"), self.password)
            return bool(separator) and valid_user and valid_password
        except (ValueError, binascii.Error, UnicodeDecodeError):
            return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in {"http", "websocket"} or not self.password:
            return await self.app(scope, receive, send)
        # 只放行平台探活；页面、工具入口和业务读写都需要密码。
        if (
            scope["type"] == "http"
            and scope["path"] == "/api/health"
            and scope["method"] in {"GET", "HEAD"}
        ) or self.authorized(scope):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = PlainTextResponse(
            "Please sign in to the BORDER demo.",
            status_code=401,
            headers={
                "WWW-Authenticate": 'Basic realm="BORDER Demo", charset="UTF-8"',
                "Cache-Control": "no-store",
            },
        )
        await response(scope, receive, send)
