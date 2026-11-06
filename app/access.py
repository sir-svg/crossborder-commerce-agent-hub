"""网页登录、短期会话和统一访问保护；纯 ASGI 中间件保留 SSE。"""

import hashlib
import hmac
import secrets
import time

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

COOKIE_NAME = "border_session"
SESSION_SECONDS = 8 * 60 * 60


class DemoAuth:
    """只保存随机会话令牌的摘要；退出和重启均可使会话失效。"""

    def __init__(self, username: str, password: str):
        self.username = username.encode("utf-8")
        self.password = password.encode("utf-8")
        self.sessions: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def authenticate(self, username: str, password: str) -> bool:
        valid_user = hmac.compare_digest(username.encode("utf-8"), self.username)
        valid_password = hmac.compare_digest(password.encode("utf-8"), self.password)
        return self.enabled and valid_user and valid_password

    def issue(self) -> str:
        now = time.monotonic()
        self.sessions = {key: expires for key, expires in self.sessions.items() if expires > now}
        # 单用户演示只保留最近的512个会话，避免无限增长。
        if len(self.sessions) >= 512:
            del self.sessions[next(iter(self.sessions))]
        token = secrets.token_urlsafe(32)
        self.sessions[self._digest(token)] = now + SESSION_SECONDS
        return token

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def valid(self, token: str) -> bool:
        key = self._digest(token)
        expires = self.sessions.get(key, 0)
        if expires <= time.monotonic():
            self.sessions.pop(key, None)
            return False
        return True

    def revoke(self, token: str):
        self.sessions.pop(self._digest(token), None)


def same_origin(request: HTTPConnection) -> bool:
    """浏览器的写请求必须来自本站；无 Origin 的服务器客户端由会话令牌认证。"""
    from urllib.parse import urlsplit

    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and parsed.netloc == request.headers.get("host")
    except ValueError:
        return False


class DemoAccess:
    PUBLIC_ASSETS = {"/static/login.css", "/static/login.js"}

    def __init__(self, app: ASGIApp, auth: DemoAuth):
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in {"http", "websocket"} or not self.auth.enabled:
            return await self.app(scope, receive, send)
        request = HTTPConnection(scope)
        path = scope["path"]
        method = scope.get("method", "GET")
        public = (path in self.PUBLIC_ASSETS and method in {"GET", "HEAD"}) or (
            path in {"/api/health", "/login"} and method in {"GET", "HEAD"}
        )
        public = public or (path in {"/login", "/logout"} and method == "POST")
        valid = self.auth.valid(request.cookies.get(COOKIE_NAME, ""))
        if public or valid:
            if method not in {"GET", "HEAD", "OPTIONS"} and not same_origin(request):
                response = JSONResponse({"detail": "请求来源不正确，请从登录页面重新进入"}, 403)
                return await response(scope, receive, send)
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if method in {"GET", "HEAD"} and not path.startswith(("/api/", "/agents/", "/static/")):
            response = RedirectResponse(
                "/login", status_code=303, headers={"Cache-Control": "no-store"}
            )
        else:
            response = JSONResponse(
                {"detail": "请先登录后再操作", "login_url": "/login"},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        await response(scope, receive, send)
