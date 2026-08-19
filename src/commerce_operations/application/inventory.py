import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.application.procurement import ProcurementService
from commerce_operations.domains.inventory import (
    InvalidInventoryChange,
    apply_quantity_change,
    weighted_cost_basis,
)
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    InventoryChangedPayload,
    LowStockPayload,
    StockReceivedPayload,
    create_event,
)
from commerce_operations.persistence.enums import (
    InventoryMovementType,
    PurchaseOrderStatus,
)
from commerce_operations.persistence.models import (
    AuditEvent,
    InventoryItem,
    InventoryMovement,
    ProcurementRequest,
    PurchaseOrder,
    SupplierQuote,
    WorkflowRun,
)


class InventoryNotFoundError(LookupError):
    pass


class PurchaseOrderNotReceivableError(RuntimeError):
    pass


class PurchaseOrderReceiptNotFoundError(LookupError):
    pass


class InventoryIdempotencyConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class InventoryChangeResult:
    inventory_item: InventoryItem
    movement: InventoryMovement
    duplicate: bool


class InventoryService:
    def __init__(
        self,
        event_store: DatabaseEventStore | None = None,
        procurement_service: ProcurementService | None = None,
    ) -> None:
        self.event_store = event_store or DatabaseEventStore()
        self.procurement_service = procurement_service or ProcurementService(self.event_store)

    def lookup(self, session: Session, sku: str) -> Sequence[InventoryItem]:
        return session.scalars(
            select(InventoryItem)
            .where(InventoryItem.sku == sku)
            .order_by(InventoryItem.storage_location)
        ).all()

    def get(
        self, session: Session, inventory_item_id: uuid.UUID, *, for_update: bool = False
    ) -> InventoryItem:
        statement = select(InventoryItem).where(InventoryItem.id == inventory_item_id)
        if for_update:
            statement = statement.with_for_update()
        item = session.scalar(statement)
        if item is None:
            raise InventoryNotFoundError(str(inventory_item_id))
        return item

    def history(
        self, session: Session, inventory_item_id: uuid.UUID, *, limit: int = 200
    ) -> Sequence[InventoryMovement]:
        self.get(session, inventory_item_id)
        return session.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.inventory_item_id == inventory_item_id)
            .order_by(InventoryMovement.created_at.desc())
            .limit(limit)
        ).all()

    def receive(
        self,
        session: Session,
        purchase_order_id: uuid.UUID,
        *,
        sku: str,
        storage_location: str,
        quantity_received: int,
        landed_unit_cost: Decimal | None,
        low_stock_threshold: int,
        actor: str,
        reason: str,
        idempotency_key: str,
        received_at: datetime | None = None,
    ) -> InventoryChangeResult:
        existing = self._existing_movement(session, idempotency_key)
        if existing is not None:
            if (
                existing.purchase_order_id != purchase_order_id
                or existing.quantity_delta != quantity_received
                or existing.movement_type is not InventoryMovementType.RECEIPT
            ):
                raise InventoryIdempotencyConflict(
                    "Receipt idempotency key was reused for different stock"
                )
            return InventoryChangeResult(existing.inventory_item, existing, True)

        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.id == purchase_order_id).with_for_update()
        )
        if purchase_order is None:
            raise PurchaseOrderReceiptNotFoundError("Purchase order was not found")
        if purchase_order.status is not PurchaseOrderStatus.SHIPPED:
            raise PurchaseOrderNotReceivableError(
                f"Purchase order must be shipped, not {purchase_order.status.value}"
            )
        remaining = purchase_order.quantity - purchase_order.received_quantity
        if quantity_received <= 0:
            raise InvalidInventoryChange("Received quantity must be positive")
        if quantity_received > remaining:
            raise InvalidInventoryChange(
                f"Received quantity {quantity_received} exceeds remaining quantity {remaining}"
            )

        procurement = session.get(ProcurementRequest, purchase_order.procurement_request_id)
        if procurement is None:
            raise PurchaseOrderNotReceivableError("Procurement request is missing")
        quote = session.get(SupplierQuote, procurement.selected_quote_id)
        if quote is None:
            raise PurchaseOrderNotReceivableError("Supplier quote is missing")
        unit_cost = landed_unit_cost if landed_unit_cost is not None else quote.unit_cost
        item = session.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.sku == sku,
                InventoryItem.storage_location == storage_location,
            )
            .with_for_update()
        )
        if item is None:
            item = InventoryItem(
                product_id=procurement.product_id,
                sku=sku,
                storage_location=storage_location,
                quantity_on_hand=0,
                reserved_quantity=0,
                cost_basis=Decimal("0"),
                currency=purchase_order.currency,
                low_stock_threshold=low_stock_threshold,
            )
            session.add(item)
            session.flush()
        elif item.product_id != procurement.product_id:
            raise InventoryIdempotencyConflict(
                "SKU and storage location already belong to another product"
            )
        elif item.currency != purchase_order.currency:
            raise InventoryIdempotencyConflict("Inventory and purchase-order currencies differ")

        previous_available = item.available_quantity
        new_cost_basis = weighted_cost_basis(
            current_quantity=item.quantity_on_hand,
            current_cost_basis=item.cost_basis,
            added_quantity=quantity_received,
            added_unit_cost=unit_cost,
        )
        balance = apply_quantity_change(
            quantity_on_hand=item.quantity_on_hand,
            reserved_quantity=item.reserved_quantity,
            quantity_delta=quantity_received,
        )
        item.quantity_on_hand = balance.quantity_on_hand
        item.cost_basis = new_cost_basis
        purchase_order.received_quantity += quantity_received
        purchase_order.actual_landed_cost = (
            purchase_order.actual_landed_cost or Decimal("0")
        ) + unit_cost * quantity_received

        movement = InventoryMovement(
            inventory_item_id=item.id,
            purchase_order_id=purchase_order.id,
            movement_type=InventoryMovementType.RECEIPT,
            quantity_delta=quantity_received,
            unit_cost=unit_cost,
            reason=reason,
            source_type="purchase_order",
            source_id=str(purchase_order.id),
            idempotency_key=idempotency_key,
        )
        session.add(movement)
        session.flush()
        self._record_change(
            session,
            item,
            movement,
            actor=actor,
            previous_available=previous_available,
            stock_received=True,
            workflow=self._workflow(session, procurement),
        )

        if purchase_order.received_quantity == purchase_order.quantity:
            self.procurement_service.mark_received(
                session,
                procurement.id,
                actor=actor,
                reason="Purchase order fully received",
                now=received_at or datetime.now(UTC),
            )
        return InventoryChangeResult(item, movement, False)

    def adjust(
        self,
        session: Session,
        inventory_item_id: uuid.UUID,
        *,
        quantity_delta: int,
        reason: str,
        actor: str,
        idempotency_key: str,
        unit_cost: Decimal | None = None,
    ) -> InventoryChangeResult:
        existing = self._existing_movement(session, idempotency_key)
        if existing is not None:
            if (
                existing.inventory_item_id != inventory_item_id
                or existing.quantity_delta != quantity_delta
                or existing.movement_type is not InventoryMovementType.ADJUSTMENT
            ):
                raise InventoryIdempotencyConflict(
                    "Adjustment idempotency key was reused for a different change"
                )
            return InventoryChangeResult(existing.inventory_item, existing, True)

        item = self.get(session, inventory_item_id, for_update=True)
        previous_available = item.available_quantity
        balance = apply_quantity_change(
            quantity_on_hand=item.quantity_on_hand,
            reserved_quantity=item.reserved_quantity,
            quantity_delta=quantity_delta,
        )
        if quantity_delta > 0 and unit_cost is not None:
            item.cost_basis = weighted_cost_basis(
                current_quantity=item.quantity_on_hand,
                current_cost_basis=item.cost_basis,
                added_quantity=quantity_delta,
                added_unit_cost=unit_cost,
            )
        item.quantity_on_hand = balance.quantity_on_hand
        movement = InventoryMovement(
            inventory_item_id=item.id,
            movement_type=InventoryMovementType.ADJUSTMENT,
            quantity_delta=quantity_delta,
            unit_cost=unit_cost,
            reason=reason,
            source_type="manual_adjustment",
            source_id=str(item.id),
            idempotency_key=idempotency_key,
        )
        session.add(movement)
        session.flush()
        self._record_change(
            session,
            item,
            movement,
            actor=actor,
            previous_available=previous_available,
            stock_received=False,
            workflow=None,
        )
        return InventoryChangeResult(item, movement, False)

    def _record_change(
        self,
        session: Session,
        item: InventoryItem,
        movement: InventoryMovement,
        *,
        actor: str,
        previous_available: int,
        stock_received: bool,
        workflow: WorkflowRun | None,
    ) -> None:
        session.flush()
        correlation_id = workflow.correlation_id if workflow else uuid.uuid4()
        session.add(
            AuditEvent(
                actor_type="user" if actor != "system" else "system",
                actor_id=actor,
                action="inventory.stock_received" if stock_received else "inventory.adjusted",
                resource_type="inventory_item",
                resource_id=item.id,
                before_state={"available_quantity": previous_available},
                after_state={
                    "quantity_on_hand": item.quantity_on_hand,
                    "reserved_quantity": item.reserved_quantity,
                    "available_quantity": item.available_quantity,
                },
                reason=movement.reason,
                correlation_id=correlation_id,
            )
        )
        common = {
            "aggregate_type": "inventory_item",
            "aggregate_id": item.id,
            "aggregate_version": item.version,
            "correlation_id": correlation_id,
            "workflow_id": workflow.id if workflow else None,
        }
        self.event_store.publish(
            session,
            create_event(
                InventoryChangedPayload(
                    inventory_item_id=item.id,
                    quantity_delta=movement.quantity_delta,
                    quantity_on_hand=item.quantity_on_hand,
                    reserved_quantity=item.reserved_quantity,
                ),
                idempotency_key=f"inventory-changed:{movement.id}",
                **common,
            ),
        )
        if stock_received:
            assert movement.purchase_order_id is not None
            self.event_store.publish(
                session,
                create_event(
                    StockReceivedPayload(
                        inventory_item_id=item.id,
                        purchase_order_id=movement.purchase_order_id,
                        quantity=movement.quantity_delta,
                    ),
                    idempotency_key=f"stock-received:{movement.id}",
                    **common,
                ),
            )
        if (
            previous_available > item.low_stock_threshold
            and item.available_quantity <= item.low_stock_threshold
        ):
            self.event_store.publish(
                session,
                create_event(
                    LowStockPayload(
                        inventory_item_id=item.id,
                        sku=item.sku,
                        available_quantity=item.available_quantity,
                        threshold=item.low_stock_threshold,
                    ),
                    idempotency_key=f"low-stock:{movement.id}",
                    **common,
                ),
            )

    @staticmethod
    def _existing_movement(session: Session, idempotency_key: str) -> InventoryMovement | None:
        return session.scalar(
            select(InventoryMovement).where(InventoryMovement.idempotency_key == idempotency_key)
        )

    @staticmethod
    def _workflow(session: Session, procurement: ProcurementRequest) -> WorkflowRun:
        workflow = session.get(WorkflowRun, procurement.workflow_run_id)
        if workflow is None:
            raise PurchaseOrderNotReceivableError("Procurement workflow is missing")
        return workflow
