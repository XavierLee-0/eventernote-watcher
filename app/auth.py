"""HTTP 鉴权中间件。

- WebUI 及所有 /api/* 接口: HTTP Basic Auth (AUTH_PASSWORD 环境变量, 为空则不启用)
- /api/calendar.ics: ?token=xxx (ICS_TOKEN 环境变量, 为空则不启用)

设计: 密码/token 只从环境变量注入, 不落库、不出现在仓库中;
比较使用常数时间比较避免时序侧信道。
"""
from __future__ import annotations

import base64
import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _check_basic_auth(header: str | None, password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except Exception:
        return False
    # 格式 user:password, 只校验密码
    _, _, pw = decoded.partition(":")
    return hmac.compare_digest(pw, password)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.password = os.environ.get("AUTH_PASSWORD", "")
        self.ics_token = os.environ.get("ICS_TOKEN", "")

    async def dispatch(self, request, call_next):
        path = request.url.path
        if path == "/api/calendar.ics":
            if self.ics_token:
                supplied = request.query_params.get("token", "")
                if not hmac.compare_digest(supplied, self.ics_token):
                    return JSONResponse({"detail": "invalid or missing token"}, status_code=401)
            return await call_next(request)
        if self.password and not _check_basic_auth(request.headers.get("authorization"), self.password):
            return JSONResponse(
                {"detail": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="eventernote-watcher"'},
            )
        return await call_next(request)
