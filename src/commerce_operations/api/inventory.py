import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce_operations.api.inventory_schemas import (
    GoodsReceiptRequest,
    InventoryAdjustmentRequest,
    InventoryChangeResponse,
    InventoryItemResponse,
    InventoryLookupResponse,
    InventoryMovementResponse,
    StockHistoryResponse,
)
from commerce_operations.application.inventory import (
    InventoryIdempotencyConflict,
    InventoryNotFoundError,
    InventoryService,
    PurchaseOrderNotReceivableError,
    PurchaseOrderReceiptNotFoundError,
)
from commerce_operations.domains.inventory import InvalidInventoryChange
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import InventoryItem, InventoryMovement

router = APIRouter(tags=["inventory"])
SessionDependency = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)]


def get_inventory_service() -> InventoryService:
    return InventoryService()


ServiceDependency = Annotated[InventoryService, Depends(get_inventory_service)]


def _item_response(item: InventoryItem) -> InventoryItemResponse:
    return InventoryItemResponse(
        id=item.id,
        sku=item.sku,
        product_id=item.product_id,
        product_name=item.product.name,
        storage_location=item.storage_location,
        quantity_on_hand=item.quantity_on_hand,
        reserved_quantity=item.reserved_quantity,
        available_quantity=item.available_quantity,
        cost_basis=item.cost_basis,
        currency=item.currency,
        low_stock_threshold=item.low_stock_threshold,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _movement_response(movement: InventoryMovement) -> InventoryMovementResponse:
    return InventoryMovementResponse(
        id=movement.id,
        inventory_item_id=movement.inventory_item_id,
        purchase_order_id=movement.purchase_order_id,
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
        unit_cost=movement.unit_cost,
        reason=movement.reason,
        source_type=movement.source_type,
        source_id=movement.source_id,
        idempotency_key=movement.idempotency_key,
        created_at=movement.created_at,
    )


def _change_response(result) -> InventoryChangeResponse:
    return InventoryChangeResponse(
        inventory=_item_response(result.inventory_item),
        movement=_movement_response(result.movement),
        duplicate=result.duplicate,
    )


def _raise_inventory_error(exc: Exception) -> None:
    if isinstance(exc, (InventoryNotFoundError, PurchaseOrderReceiptNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            InvalidInventoryChange,
            InventoryIdempotencyConflict,
            PurchaseOrderNotReceivableError,
        ),
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


@router.post(
    "/purchase-orders/{purchase_order_id}/receive",
    response_model=InventoryChangeResponse,
    summary="Receive purchased goods into inventory",
)
def receive_stock(
    purchase_order_id: uuid.UUID,
    command: GoodsReceiptRequest,
    idempotency_key: IdempotencyKey,
    session: SessionDependency,
    service: ServiceDependency,
) -> InventoryChangeResponse:
    kwargs = {
        "sku": command.sku,
        "storage_location": command.storage_location,
        "quantity_received": command.quantity_received,
        "landed_unit_cost": command.landed_unit_cost,
        "low_stock_threshold": command.low_stock_threshold,
        "actor": command.actor,
        "reason": command.reason,
        "idempotency_key": idempotency_key,
        "received_at": command.received_at,
    }
    try:
        result = service.receive(session, purchase_order_id, **kwargs)
        session.commit()
        return _change_response(result)
    except IntegrityError:
        session.rollback()
        try:
            result = service.receive(session, purchase_order_id, **kwargs)
            session.commit()
            return _change_response(result)
        except Exception as exc:
            session.rollback()
            _raise_inventory_error(exc)
            raise
    except Exception as exc:
        session.rollback()
        _raise_inventory_error(exc)
        raise


@router.get(
    "/inventory/{sku}", response_model=InventoryLookupResponse, summary="Look up inventory by SKU"
)
def lookup_inventory(
    sku: str,
    session: SessionDependency,
    service: ServiceDependency,
) -> InventoryLookupResponse:
    items = [_item_response(item) for item in service.lookup(session, sku)]
    return InventoryLookupResponse(items=items, count=len(items))


@router.post(
    "/inventory/items/{inventory_item_id}/adjustments",
    response_model=InventoryChangeResponse,
    summary="Adjust inventory",
)
def adjust_inventory(
    inventory_item_id: uuid.UUID,
    command: InventoryAdjustmentRequest,
    idempotency_key: IdempotencyKey,
    session: SessionDependency,
    service: ServiceDependency,
) -> InventoryChangeResponse:
    kwargs = {
        "quantity_delta": command.quantity_delta,
        "unit_cost": command.unit_cost,
        "actor": command.actor,
        "reason": command.reason,
        "idempotency_key": idempotency_key,
    }
    try:
        result = service.adjust(session, inventory_item_id, **kwargs)
        session.commit()
        return _change_response(result)
    except IntegrityError:
        session.rollback()
        try:
            result = service.adjust(session, inventory_item_id, **kwargs)
            session.commit()
            return _change_response(result)
        except Exception as exc:
            session.rollback()
            _raise_inventory_error(exc)
            raise
    except Exception as exc:
        session.rollback()
        _raise_inventory_error(exc)
        raise


@router.get(
    "/inventory/items/{inventory_item_id}/movements",
    response_model=StockHistoryResponse,
    summary="Get stock movement history",
)
def stock_history(
    inventory_item_id: uuid.UUID,
    session: SessionDependency,
    service: ServiceDependency,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> StockHistoryResponse:
    try:
        movements = service.history(session, inventory_item_id, limit=limit)
        items = [_movement_response(movement) for movement in movements]
        return StockHistoryResponse(items=items, count=len(items))
    except Exception as exc:
        _raise_inventory_error(exc)
        raise
