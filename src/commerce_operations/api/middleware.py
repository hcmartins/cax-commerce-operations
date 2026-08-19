import logging
import time
from uuid import UUID, uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from commerce_operations.observability.logging import (
    correlation_id_context,
    request_id_context,
    workflow_id_context,
)
from commerce_operations.observability.metrics import metrics

logger = logging.getLogger(__name__)


def _valid_correlation_id(value: str | None) -> str:
    if value:
        try:
            return str(UUID(value))
        except ValueError:
            pass
    return str(uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = _valid_correlation_id(request.headers.get("X-Correlation-ID"))
        request_id = _valid_correlation_id(request.headers.get("X-Request-ID"))
        workflow_id = request.headers.get("X-Workflow-ID")
        if workflow_id:
            workflow_id = _valid_correlation_id(workflow_id)
        request.state.correlation_id = correlation_id
        request.state.request_id = request_id
        request.state.workflow_id = workflow_id
        tokens = (
            correlation_id_context.set(correlation_id),
            request_id_context.set(request_id),
            workflow_id_context.set(workflow_id),
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Request-ID"] = request_id
            if workflow_id:
                response.headers["X-Workflow-ID"] = workflow_id
            duration = (time.perf_counter() - started) * 1000
            metrics.increment(
                "commerce_http_requests_total",
                method=request.method,
                status=str(response.status_code),
            )
            metrics.increment("commerce_http_request_duration_ms_total", duration)
            logger.info(
                "request_completed method=%s path=%s status=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                duration,
            )
            return response
        finally:
            correlation_id_context.reset(tokens[0])
            request_id_context.reset(tokens[1])
            workflow_id_context.reset(tokens[2])
