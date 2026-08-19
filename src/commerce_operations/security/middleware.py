import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, settings) -> None:
        super().__init__(app)
        self.settings = settings
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {"/health", "/ready"}:
            return await call_next(request)
        identity, roles = self._authenticate(request)
        auth_required = self.settings.api_auth_enabled or self.settings.is_production
        if auth_required and identity is None:
            return self._deny(401, "Authentication required")
        request.state.principal = identity or "anonymous"
        request.state.roles = roles
        if auth_required and not self._authorized(request, roles):
            return self._deny(403, "Insufficient permissions")
        if not self._within_limit(identity or request.client.host if request.client else "unknown"):
            return self._deny(429, "Rate limit exceeded", {"Retry-After": "1"})
        return await call_next(request)

    def _authenticate(self, request: Request) -> tuple[str | None, set[str]]:
        presented = request.headers.get("X-API-Key", "")
        if not presented:
            return None, set()
        for identity, secret in self.settings.api_keys.items():
            if hmac.compare_digest(
                hashlib.sha256(presented.encode()).digest(),
                hashlib.sha256(secret.get_secret_value().encode()).digest(),
            ):
                return identity, set(self.settings.api_roles.get(identity, []))
        return None, set()

    @staticmethod
    def _authorized(request: Request, roles: set[str]) -> bool:
        if "admin" in roles:
            return True
        if request.url.path == "/metrics":
            return False
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return bool(roles & {"viewer", "operator", "approver"})
        if "/approvals/" in request.url.path and request.url.path.endswith(("/approve", "/reject")):
            return "approver" in roles
        return "operator" in roles

    def _within_limit(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._requests[key]
        boundary = now - self.settings.rate_limit_window_seconds
        while bucket and bucket[0] <= boundary:
            bucket.popleft()
        if len(bucket) >= self.settings.rate_limit_requests:
            return False
        bucket.append(now)
        return True

    @staticmethod
    def _deny(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"detail": detail}, status_code=status, headers=headers)
