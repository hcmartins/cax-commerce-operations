from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import SQLAlchemyError

from commerce_operations.api.schemas import HealthResponse
from commerce_operations.config import Settings, get_settings
from commerce_operations.observability.metrics import metrics
from commerce_operations.persistence.database import database_is_ready

router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health(settings: SettingsDependency) -> HealthResponse:
    return HealthResponse(status="ok", service=settings.app_name, version=settings.app_version)


@router.get("/ready", response_model=HealthResponse, summary="Readiness probe")
def ready(settings: SettingsDependency) -> HealthResponse:
    try:
        database_is_ready()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from exc
    return HealthResponse(status="ready", service=settings.app_name, version=settings.app_version)


@router.get("/metrics", include_in_schema=False)
def application_metrics(settings: SettingsDependency) -> PlainTextResponse:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics are disabled")
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
