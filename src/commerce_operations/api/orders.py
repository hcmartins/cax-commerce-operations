import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce_operations.api.order_schemas import (
    FulfilmentStateResponse,
    OrderIngestionResponse,
    OrderItemResponse,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from commerce_operations.application.orders import (
    InsufficientInventoryError,
    OrderIdempotencyConflict,
    OrderNotFoundError,
    OrderService,
    OrderValidationError,
)
from commerce_operations.domains.orders import InvalidOrderTransition
from commerce_operations.integrations.marketplaces.orders import NormalizedMarketplaceOrder
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import Order

router = APIRouter(prefix="/orders", tags=["orders"])
SessionDependency = Annotated[Session, Depends(get_session)]


def get_order_service() -> OrderService:
    return OrderService()


ServiceDependency = Annotated[OrderService, Depends(get_order_service)]


def _response(order: Order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        marketplace=order.marketplace,
        marketplace_account_id=order.marketplace_account_id,
        external_order_id=order.external_order_id,
        status=order.status,
        currency=order.currency,
        total_amount=order.total_amount,
        customer_reference=order.customer_reference,
        ordered_at=order.ordered_at,
        paid_at=order.paid_at,
        dispatched_at=order.dispatched_at,
        delivered_at=order.delivered_at,
        workflow_run_id=order.workflow_run_id,
        items=[
            OrderItemResponse(
                id=item.id,
                external_line_id=item.external_line_id,
                inventory_item_id=item.inventory_item_id,
                sku=item.sku,
                quantity=item.quantity,
                unit_price=item.unit_price,
                tax_amount=item.tax_amount,
                discount_amount=item.discount_amount,
                reservation_reference=item.reservation_reference,
            )
            for item in order.items
        ],
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def _raise_order_error(exc: Exception) -> None:
    if isinstance(exc, OrderNotFoundError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found") from exc
    if isinstance(
        exc,
        (
            InsufficientInventoryError,
            OrderIdempotencyConflict,
            OrderValidationError,
            InvalidOrderTransition,
        ),
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    raise exc


@router.post("", response_model=OrderIngestionResponse, status_code=status.HTTP_201_CREATED)
def ingest_order(
    command: NormalizedMarketplaceOrder,
    session: SessionDependency,
    service: ServiceDependency,
) -> OrderIngestionResponse:
    try:
        result = service.ingest(session, command)
        session.commit()
        return OrderIngestionResponse(order=_response(result.order), duplicate=result.duplicate)
    except IntegrityError:
        session.rollback()
        try:
            result = service.ingest(session, command)
            session.commit()
            return OrderIngestionResponse(order=_response(result.order), duplicate=True)
        except Exception as exc:
            session.rollback()
            _raise_order_error(exc)
            raise
    except Exception as exc:
        session.rollback()
        _raise_order_error(exc)
        raise


@router.get("", response_model=OrderListResponse)
def list_orders(
    session: SessionDependency,
    service: ServiceDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> OrderListResponse:
    items = [_response(order) for order in service.list(session, limit=limit)]
    return OrderListResponse(items=items, count=len(items))


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: uuid.UUID, session: SessionDependency, service: ServiceDependency
) -> OrderResponse:
    try:
        return _response(service.get(session, order_id))
    except Exception as exc:
        _raise_order_error(exc)
        raise


@router.post("/{order_id}/status", response_model=OrderResponse)
def update_order_status(
    order_id: uuid.UUID,
    command: OrderStatusUpdate,
    session: SessionDependency,
    service: ServiceDependency,
) -> OrderResponse:
    try:
        order = service.update_status(
            session,
            order_id,
            command.status,
            actor=command.actor,
            reason=command.reason,
        )
        session.commit()
        return _response(order)
    except Exception as exc:
        session.rollback()
        _raise_order_error(exc)
        raise


@router.get("/{order_id}/fulfilment", response_model=FulfilmentStateResponse)
def fulfilment_state(
    order_id: uuid.UUID, session: SessionDependency, service: ServiceDependency
) -> FulfilmentStateResponse:
    try:
        order = service.get(session, order_id)
        workflow = order.workflow_run
        return FulfilmentStateResponse(
            order_id=order.id,
            order_status=order.status,
            workflow_run_id=order.workflow_run_id,
            workflow_status=workflow.status if workflow else None,
            current_step=workflow.current_step if workflow else None,
            reserved_items=sum(item.quantity for item in order.items)
            if order.status.value in {"pending", "paid", "processing"}
            else 0,
            dispatched_at=order.dispatched_at,
            delivered_at=order.delivered_at,
        )
    except Exception as exc:
        _raise_order_error(exc)
        raise
