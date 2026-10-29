"""API/MCP Bearer-token. HA ingress (ingelogde gebruiker) mag zonder token."""

from __future__ import annotations

import hmac
from typing import Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from kippiesearch.config import Settings

OPEN_PATHS = {
    "/health",
    "/api/health",
}
OPEN_PREFIXES = (
    "/static/",
    "/assets/",
)


def is_ingress(request: Request) -> bool:
    headers = request.headers
    if headers.get("x-hass-is-admin") is not None:
        return True
    if headers.get("x-remote-user-id"):
        return True
    if headers.get("x-ingress-path"):
        return True
    # Supervisor ingress proxy
    forwarded = headers.get("x-forwarded-host", "")
    if "ingress" in forwarded.lower():
        return True
    return False


def token_ok(settings: Settings, header: str | None) -> bool:
    if not settings.require_auth():
        return True
    if not header:
        return False
    scheme, _, value = header.partition(" ")
    supplied = value.strip() if scheme.lower() == "bearer" else header.strip()
    expected = settings.api_token.encode("utf-8")
    given = supplied.encode("utf-8")
    if len(given) != len(expected):
        # vergelijk toch met dummy om timing leaks te beperken
        hmac.compare_digest(expected, expected)
        return False
    return hmac.compare_digest(given, expected)


def protect(settings: Settings) -> Callable:
    async def dependency(request: Request) -> None:
        if is_open(request) or is_ingress(request):
            return
        if token_ok(settings, request.headers.get("authorization")):
            return
        raise HTTPException(status_code=401, detail="Bearer token vereist")

    return dependency


def is_open(request: Request) -> bool:
    path = request.url.path
    if path in OPEN_PATHS or path == "/":
        return True
    return any(path.startswith(prefix) for prefix in OPEN_PREFIXES)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in OPEN_PATHS or path == "/" or path.startswith("/static/"):
            return await call_next(request)
        if is_ingress(request):
            return await call_next(request)
        if not self.settings.require_auth():
            return await call_next(request)
        if token_ok(self.settings, request.headers.get("authorization")):
            return await call_next(request)
        return JSONResponse({"detail": "Bearer token vereist"}, status_code=401)
