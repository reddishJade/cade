"""Web 控制面的主机、来源和令牌校验。"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class WebSecurityMiddleware:
    """在 HTTP 路由和 WebSocket 握手前校验访问边界。"""

    def __init__(
        self, app: ASGIApp, *, token: str, allowed_origins: tuple[str, ...]
    ) -> None:
        if not token or not allowed_origins:
            raise ValueError("Web authentication requires a token and trusted origins")
        self.app = app
        self.token = token
        self.origins = frozenset(allowed_origins)
        self.authorities: set[str] = set()
        for origin in allowed_origins:
            url = urlsplit(origin)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.path
                or url.query
                or url.fragment
                or url.hostname in {"0.0.0.0", "::", "*"}
            ):
                raise ValueError(f"Invalid trusted origin: {origin}")
            self.authorities.add(url.netloc)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        origins = headers.getlist("origin")
        websocket = scope["type"] == "websocket"
        unsafe = websocket or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}
        status = 0
        if len(hosts) != 1 or hosts[0] not in self.authorities:
            status = 400
        elif (
            len(origins) > 1
            or (origins and origins[0] not in self.origins)
            or (unsafe and not origins)
        ):
            status = 403
        elif websocket:
            protocols = scope.get("subprotocols", [])
            if (
                len(protocols) != 2
                or protocols[0] != "cade"
                or not self._matches(protocols[1])
            ):
                status = 401
        elif scope["path"] == "/api" or scope["path"].startswith("/api/"):
            auth = headers.getlist("authorization")
            if (
                len(auth) != 1
                or not auth[0].startswith("Bearer ")
                or not self._matches(auth[0][7:])
            ):
                status = 401
        if status:
            if websocket:
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = JSONResponse(
                    {"error": "Web access denied"},
                    status_code=status,
                    headers={"Cache-Control": "no-store"},
                )
                await response(scope, receive, send)
            return

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"content-security-policy", b"frame-ancestors 'none'"),
                    ]
                )
            await send(message)

        await self.app(scope, receive, secured_send)

    def _matches(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate.encode("utf-8"), self.token.encode())


def web_origins(host: str, port: int) -> tuple[str, ...]:
    """从绑定地址生成明确的来源；通配绑定只信任本机地址。"""
    host = host.lower()
    if host in {"127.0.0.1", "localhost", "::1", "0.0.0.0", "::"}:
        primary = "[::1]" if host == "::1" else host
        loopback_hosts = ("127.0.0.1", "localhost", "[::1]")
        hosts = (
            (primary, *(name for name in loopback_hosts if name != primary))
            if primary in loopback_hosts
            else loopback_hosts
        )
    else:
        hosts = (f"[{host}]" if ":" in host else host,)
    suffix = "" if port == 80 else f":{port}"
    return tuple(f"http://{name}{suffix}" for name in hosts)
