import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from commerce_operations.api.schemas import ProblemDetail

logger = logging.getLogger(__name__)


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "unknown")


def _problem(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str,
    errors: list[dict] | None = None,
) -> JSONResponse:
    body = ProblemDetail(
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        correlation_id=_correlation_id(request),
        errors=errors,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return _problem(
            request,
            status_code=exc.status_code,
            title="Request failed",
            detail=str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _problem(
            request,
            status_code=422,
            title="Validation error",
            detail="The request did not pass validation",
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", exc_info=exc)
        reporter = getattr(request.app.state, "error_reporter", None)
        if reporter is not None:
            try:
                reporter.capture_exception(
                    exc,
                    correlation_id=_correlation_id(request),
                    request_id=getattr(request.state, "request_id", "unknown"),
                    path=request.url.path,
                )
            except Exception:
                logger.exception("error_reporting_hook_failed")
        return _problem(
            request,
            status_code=500,
            title="Internal server error",
            detail="An unexpected error occurred",
        )
