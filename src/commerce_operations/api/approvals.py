import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from commerce_operations.api.approval_schemas import (
    ApprovalDecisionRequest,
    ApprovalListResponse,
    ApprovalResponse,
)
from commerce_operations.application.customer_service import (
    register_customer_response_approval_handler,
)
from commerce_operations.application.listings import register_listing_approval_handler
from commerce_operations.application.pricing import register_pricing_approval_handler
from commerce_operations.application.procurement import register_procurement_approval_handler
from commerce_operations.application.refunds import register_refund_approval_handler
from commerce_operations.approvals.engine import (
    ApprovalActionRegistry,
    ApprovalEngine,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    ApprovalTransitionError,
)
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.config import Settings, get_settings
from commerce_operations.persistence.database import get_session

router = APIRouter(prefix="/approvals", tags=["approvals"])
SessionDependency = Annotated[Session, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def get_approval_engine(settings: SettingsDependency) -> ApprovalEngine:
    action_registry = ApprovalActionRegistry()
    register_procurement_approval_handler(action_registry)
    register_listing_approval_handler(action_registry)
    register_pricing_approval_handler(action_registry)
    register_customer_response_approval_handler(action_registry)
    register_refund_approval_handler(action_registry)
    return ApprovalEngine(
        ApprovalPolicy.from_settings(settings),
        default_expiry_hours=settings.default_approval_expiry_hours,
        action_registry=action_registry,
    )


EngineDependency = Annotated[ApprovalEngine, Depends(get_approval_engine)]


def _authenticated_actor(request: Request, claimed_actor: str) -> str:
    principal = getattr(request.state, "principal", "anonymous")
    return claimed_actor if principal == "anonymous" else principal


@router.get("", response_model=ApprovalListResponse, summary="List pending approvals")
def list_pending_approvals(
    session: SessionDependency,
    engine: EngineDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApprovalListResponse:
    records = engine.list_pending(session, limit=limit)
    items = [ApprovalResponse.from_record(record) for record in records]
    return ApprovalListResponse(items=items, count=len(items))


@router.get("/{approval_id}", response_model=ApprovalResponse, summary="Inspect approval")
def get_approval(
    approval_id: uuid.UUID,
    session: SessionDependency,
    engine: EngineDependency,
) -> ApprovalResponse:
    try:
        return ApprovalResponse.from_record(engine.get(session, approval_id))
    except ApprovalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found"
        ) from exc


@router.post("/{approval_id}/approve", response_model=ApprovalResponse, summary="Approve action")
def approve(
    approval_id: uuid.UUID,
    decision: ApprovalDecisionRequest,
    request: Request,
    session: SessionDependency,
    engine: EngineDependency,
) -> ApprovalResponse:
    try:
        record = engine.approve(
            session,
            approval_id,
            approver=_authenticated_actor(request, decision.approver),
            reason=decision.reason,
        )
        session.commit()
        return ApprovalResponse.from_record(record)
    except ApprovalExpiredError as exc:
        session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ApprovalNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found"
        ) from exc
    except ApprovalTransitionError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise


@router.post("/{approval_id}/reject", response_model=ApprovalResponse, summary="Reject action")
def reject(
    approval_id: uuid.UUID,
    decision: ApprovalDecisionRequest,
    request: Request,
    session: SessionDependency,
    engine: EngineDependency,
) -> ApprovalResponse:
    try:
        record = engine.reject(
            session,
            approval_id,
            approver=_authenticated_actor(request, decision.approver),
            reason=decision.reason,
        )
        session.commit()
        return ApprovalResponse.from_record(record)
    except ApprovalExpiredError as exc:
        session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ApprovalNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found"
        ) from exc
    except ApprovalTransitionError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        session.rollback()
        raise
