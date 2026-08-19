import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from commerce_operations.api.approvals import get_approval_engine
from commerce_operations.api.procurement_schemas import (
    ApprovalCreatedResponse,
    ApprovalRequestCommand,
    MarkOrderedCommand,
    MarkShippedCommand,
    ProcurementListResponse,
    ProcurementReview,
    PurchaseOrderReview,
    QuoteReview,
    StatusCommand,
    SupplierReview,
)
from commerce_operations.application.procurement import (
    ProcurementNotFoundError,
    ProcurementService,
    ProcurementValidationError,
    PurchaseOrderNotFoundError,
)
from commerce_operations.approvals.engine import ApprovalEngine
from commerce_operations.domains.procurement import InvalidProcurementTransition
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import ProcurementRequest

router = APIRouter(prefix="/procurement-requests", tags=["procurement"])
SessionDependency = Annotated[Session, Depends(get_session)]
ApprovalEngineDependency = Annotated[ApprovalEngine, Depends(get_approval_engine)]


def get_procurement_service() -> ProcurementService:
    return ProcurementService()


ServiceDependency = Annotated[ProcurementService, Depends(get_procurement_service)]


def _review(record: ProcurementRequest) -> ProcurementReview:
    quote = record.selected_quote
    supplier = quote.supplier
    purchase_order = record.purchase_order
    return ProcurementReview(
        id=record.id,
        product_id=record.product_id,
        product_name=record.product.name,
        status=record.status,
        requested_quantity=record.requested_quantity,
        estimated_landed_cost_per_unit=record.estimated_landed_cost,
        expected_landed_cost_total=record.estimated_landed_cost * record.requested_quantity,
        currency=record.currency,
        expected_arrival_at=record.expected_arrival_at,
        supplier=SupplierReview(
            id=supplier.id,
            name=supplier.name,
            source_supplier_id=supplier.external_supplier_id,
        ),
        quote=QuoteReview(
            id=quote.id,
            source_quote_id=quote.external_quote_id,
            moq=quote.moq,
            quantity=quote.quantity,
            unit_cost=quote.unit_cost,
            shipping_cost=quote.shipping_cost,
            currency=quote.currency,
            lead_time_days=quote.lead_time_days,
            valid_until=quote.valid_until,
        ),
        purchase_order=PurchaseOrderReview(
            id=purchase_order.id,
            po_number=purchase_order.po_number,
            status=purchase_order.status,
            quantity=purchase_order.quantity,
            total_amount=purchase_order.total_amount,
            currency=purchase_order.currency,
            external_reference=purchase_order.external_reference,
            ordered_at=purchase_order.ordered_at,
            shipped_at=purchase_order.shipped_at,
            received_at=purchase_order.received_at,
            expected_arrival_at=purchase_order.expected_arrival_at,
        )
        if purchase_order
        else None,
        workflow_run_id=record.workflow_run_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _raise_service_error(exc: Exception) -> None:
    if isinstance(exc, (ProcurementNotFoundError, PurchaseOrderNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, InvalidProcurementTransition):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, ProcurementValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    raise exc


@router.get("", response_model=ProcurementListResponse, summary="List procurement requests")
def list_procurement_requests(
    session: SessionDependency,
    service: ServiceDependency,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ProcurementListResponse:
    items = [_review(record) for record in service.list(session, limit=limit)]
    return ProcurementListResponse(items=items, count=len(items))


@router.get("/{procurement_id}", response_model=ProcurementReview, summary="Review procurement")
def review_procurement(
    procurement_id: uuid.UUID,
    session: SessionDependency,
    service: ServiceDependency,
) -> ProcurementReview:
    try:
        return _review(service.get(session, procurement_id))
    except Exception as exc:
        _raise_service_error(exc)
        raise


@router.post(
    "/{procurement_id}/submit-for-approval",
    response_model=ApprovalCreatedResponse,
    summary="Request financial approval",
)
def submit_for_approval(
    procurement_id: uuid.UUID,
    command: ApprovalRequestCommand,
    session: SessionDependency,
    service: ServiceDependency,
    approval_engine: ApprovalEngineDependency,
) -> ApprovalCreatedResponse:
    try:
        approval = service.submit_for_approval(
            session,
            procurement_id,
            requester=command.requester,
            reason=command.reason,
            approval_engine=approval_engine,
        )
        session.commit()
        return ApprovalCreatedResponse(
            procurement=_review(service.get(session, procurement_id)),
            approval_id=approval.id,
            approval_status=approval.status.value,
        )
    except Exception as exc:
        session.rollback()
        _raise_service_error(exc)
        raise


def _status_change(
    session: Session,
    service: ProcurementService,
    operation,
    procurement_id: uuid.UUID,
    **kwargs,
) -> ProcurementReview:
    try:
        operation(session, procurement_id, **kwargs)
        session.commit()
        return _review(service.get(session, procurement_id))
    except Exception as exc:
        session.rollback()
        _raise_service_error(exc)
        raise


@router.post("/{procurement_id}/order", response_model=ProcurementReview)
def mark_ordered(
    procurement_id: uuid.UUID,
    command: MarkOrderedCommand,
    session: SessionDependency,
    service: ServiceDependency,
) -> ProcurementReview:
    return _status_change(
        session,
        service,
        service.mark_ordered,
        procurement_id,
        actor=command.actor,
        reason=command.reason,
        external_reference=command.external_reference,
    )


@router.post("/{procurement_id}/mark-shipped", response_model=ProcurementReview)
def mark_shipped(
    procurement_id: uuid.UUID,
    command: MarkShippedCommand,
    session: SessionDependency,
    service: ServiceDependency,
) -> ProcurementReview:
    return _status_change(
        session,
        service,
        service.mark_shipped,
        procurement_id,
        actor=command.actor,
        reason=command.reason,
        expected_arrival_at=command.expected_arrival_at,
    )


@router.post("/{procurement_id}/cancel", response_model=ProcurementReview)
def cancel(
    procurement_id: uuid.UUID,
    command: StatusCommand,
    session: SessionDependency,
    service: ServiceDependency,
) -> ProcurementReview:
    return _status_change(
        session,
        service,
        service.cancel,
        procurement_id,
        actor=command.actor,
        reason=command.reason,
    )
