from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ApiInfoResponse(BaseModel):
    name: str
    version: str


class ProblemDetail(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    correlation_id: str
    errors: list[dict[str, Any]] | None = Field(default=None)
