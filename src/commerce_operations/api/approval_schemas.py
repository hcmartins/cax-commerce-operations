import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from commerce_operations.persistence.enums import ApprovalStatus


class ApprovalDecisionRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_type: str
    resource_type: str
    resource_id: uuid.UUID
    requested_action: dict[str, Any]
    reason: str
    risk_level: str
    rule_name: str
    rule_version: int
    status: ApprovalStatus
    requester: str
    approver: str | None
    decision_reason: str | None
    workflow_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    decided_at: datetime | None
    resumed_at: datetime | None

    @classmethod
    def from_record(cls, record) -> "ApprovalResponse":
        return cls(
            id=record.id,
            action_type=record.action_type,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            requested_action=record.requested_payload,
            reason=record.requested_reason,
            risk_level=record.risk_level,
            rule_name=record.rule_name,
            rule_version=record.rule_version,
            status=record.status,
            requester=record.requested_by,
            approver=record.decided_by,
            decision_reason=record.rationale,
            workflow_run_id=record.workflow_run_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
            expires_at=record.expires_at,
            decided_at=record.decided_at,
            resumed_at=record.resumed_at,
        )


class ApprovalListResponse(BaseModel):
    items: list[ApprovalResponse]
    count: int
