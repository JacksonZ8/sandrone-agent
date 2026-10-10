from __future__ import annotations

import json
import os
from urllib.parse import urlparse

LOCAL_HOSTNAMES = {"localhost", "127.0.0.1"}
LOCAL_HEADER = b"x-sandrone-local"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TAILNET_SUFFIX = ".ts.net"
TAILSCALE_LOGIN_HEADER = b"tailscale-user-login"


def _env_logins() -> set[str]:
    """允许经 Tailscale Serve 远程访问的 tailnet 账号（逗号分隔）；为空时不允许任何远程访问。"""
    raw = os.getenv("SANDRONE_TAILSCALE_LOGINS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _hostname(value: str) -> str:
    """Extract a lowercase hostname from a Host header value or an origin URL."""
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    try:
        return (urlparse(value).hostname or "").lower()
    except ValueError:
        return ""


class LocalRequestGuard:
    """只服务本机页面：拒绝非本机 Host（防 DNS 重绑定），并要求 /api 变更请求带本地标记头。

    自定义请求头会迫使浏览器先发 CORS 预检，而 CORS 只放行本机前端，
    因此其他网页无法用无预检的简单请求伪造对话、发送微信或触发工具。
    """

    def __init__(
        self,
        app,
        *,
        allowed_hosts: set[str] | None = None,
        tailnet_logins: set[str] | None = None,
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts or LOCAL_HOSTNAMES
        self.tailnet_logins = _env_logins() if tailnet_logins is None else tailnet_logins

    def _remote_host(self, host: str, headers: dict[bytes, bytes]) -> str | None:
        """经 Tailscale Serve（HTTPS，仅 tailnet 内可达）来的请求：Host 是本机的 *.ts.net 名字，
        Serve 会附带访问者的 Tailscale-User-Login。只有登录名在白名单里才放行，返回该 Host。

        带了 Tailscale 身份头的请求一律按远程处理，即使 Host 被改写成本机地址。
        不要用 Tailscale Funnel：那会把服务暴露到公网。"""
        login = headers.get(TAILSCALE_LOGIN_HEADER, b"").decode("latin-1").strip().lower()
        if host.endswith(TAILNET_SUFFIX) or login:
            if login and login in self.tailnet_logins:
                return host
            return None
        return host if host in self.allowed_hosts else None

    def _origin_allowed(self, origin: str, host: str) -> bool:
        origin_host = _hostname(origin)
        return origin_host in self.allowed_hosts or (bool(origin_host) and origin_host == host)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await self._guard_websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        host = self._remote_host(_hostname(headers.get(b"host", b"").decode("latin-1")), headers)
        if host is None:
            await self._reject(send, 400, "请求的 Host 不是本机地址，或远程访问者不在允许名单里。")
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if path.startswith("/api/") and method not in SAFE_METHODS:
            if headers.get(LOCAL_HEADER, b"").decode("latin-1") != "1":
                await self._reject(send, 403, "变更请求缺少本地来源标记。")
                return
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin and not self._origin_allowed(origin, host):
                await self._reject(send, 403, "变更请求来源不是本机页面。")
                return

        await self.app(scope, receive, send)

    async def _guard_websocket(self, scope, receive, send) -> None:
        """WebSocket 不受 CORS 约束，浏览器却总会带 Origin：要求 Host 合格且 Origin 是本机或同一远程 Host，
        防止其他网页连上来偷听麦克风识别结果或冒充用户说话。"""
        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        host = self._remote_host(_hostname(headers.get(b"host", b"").decode("latin-1")), headers)
        origin = headers.get(b"origin", b"").decode("latin-1")
        if host is None or not self._origin_allowed(origin, host):
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
