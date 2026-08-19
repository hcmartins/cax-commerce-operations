import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from commerce_operations.domains.orders import transition_order
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    InventoryChangedPayload,
    LowStockPayload,
    OrderCancelledPayload,
    OrderReceivedPayload,
    create_event,
)
from commerce_operations.integrations.marketplaces.orders import NormalizedMarketplaceOrder
from commerce_operations.persistence.enums import (
    InventoryMovementType,
    OrderStatus,
    RunStatus,
)
from commerce_operations.persistence.models import (
    AuditEvent,
    InventoryItem,
    InventoryMovement,
    Order,
    OrderItem,
    WorkflowRun,
)


class OrderNotFoundError(LookupError):
    pass


class InsufficientInventoryError(RuntimeError):
    pass


class OrderIdempotencyConflict(RuntimeError):
    pass


class OrderValidationError(ValueError):
    pass


@dataclass(frozen=True)
class OrderIngestionResult:
    order: Order
    duplicate: bool


class OrderService:
    workflow_version = 1

    def __init__(self, event_store: DatabaseEventStore | None = None) -> None:
        self.event_store = event_store or DatabaseEventStore()

    def ingest(self, session: Session, command: NormalizedMarketplaceOrder) -> OrderIngestionResult:
        payload_hash = self._payload_hash(command)
        existing = session.scalar(
            select(Order).where(
                Order.marketplace == command.marketplace,
                Order.marketplace_account_id == command.marketplace_account_id,
                Order.external_order_id == command.external_order_id,
            )
        )
        if existing is not None:
            if existing.source_payload_hash != payload_hash:
                raise OrderIdempotencyConflict(
                    "Marketplace order was redelivered with different business data"
                )
            return OrderIngestionResult(existing, True)
        event_order = session.scalar(
            select(Order).where(
                Order.marketplace == command.marketplace,
                Order.marketplace_account_id == command.marketplace_account_id,
                Order.source_event_id == command.source_event_id,
            )
        )
        if event_order is not None:
            raise OrderIdempotencyConflict("Marketplace event ID was reused for another order")
        self._validate(command)

        correlation_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"order:{command.marketplace}:{command.marketplace_account_id}:"
            f"{command.external_order_id}",
        )
        workflow = WorkflowRun(
            workflow_name="marketplace-order-fulfilment",
            workflow_version=self.workflow_version,
            correlation_id=correlation_id,
            status=RunStatus.RUNNING,
            current_step=("paid" if command.status is OrderStatus.PAID else "awaiting_payment"),
            checkpoints={"external_order_id": command.external_order_id},
        )
        session.add(workflow)
        session.flush()
        order = Order(
            workflow_run_id=workflow.id,
            marketplace=command.marketplace,
            marketplace_account_id=command.marketplace_account_id,
            external_order_id=command.external_order_id,
            source_event_id=command.source_event_id,
            source_payload_hash=payload_hash,
            status=command.status,
            currency=command.currency.upper(),
            total_amount=command.total_amount,
            customer_reference=command.customer_reference,
            shipping_details=command.shipping_details,
            ordered_at=command.ordered_at,
            paid_at=datetime.now(UTC) if command.status is OrderStatus.PAID else None,
        )
        session.add(order)
        session.flush()

        for line in command.items:
            inventory = self._reserve(
                session,
                sku=line.sku,
                quantity=line.quantity,
                order=order,
                external_line_id=line.external_line_id,
                correlation_id=correlation_id,
            )
            order.items.append(
                OrderItem(
                    inventory_item_id=inventory.id,
                    external_line_id=line.external_line_id,
                    sku=line.sku,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_amount=line.tax_amount,
                    discount_amount=line.discount_amount,
                    reservation_reference=f"order:{order.id}:{line.external_line_id}",
                )
            )
        session.flush()
        self.event_store.publish(
            session,
            create_event(
                OrderReceivedPayload(
                    order_id=order.id,
                    marketplace=order.marketplace,
                    external_order_id=order.external_order_id,
                ),
                aggregate_type="order",
                aggregate_id=order.id,
                aggregate_version=order.version,
                correlation_id=correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"order-received:{order.id}",
            ),
        )
        self._audit(session, order, "order.received", "marketplace", None, order.status.value)
        return OrderIngestionResult(order, False)

    def update_status(
        self,
        session: Session,
        order_id: uuid.UUID,
        target: OrderStatus,
        *,
        actor: str,
        reason: str,
    ) -> Order:
        order = self.get(session, order_id, for_update=True)
        previous = order.status
        transition_order(previous, target)
        if target is previous:
            return order
        if target is OrderStatus.CANCELLED:
            self._release_order(session, order)
        elif target is OrderStatus.DISPATCHED:
            self._ship_order(session, order)
        now = datetime.now(UTC)
        order.status = target
        if target is OrderStatus.PAID:
            order.paid_at = now
        elif target is OrderStatus.DISPATCHED:
            order.dispatched_at = now
        elif target is OrderStatus.DELIVERED:
            order.delivered_at = now
        workflow = order.workflow_run
        if workflow is not None:
            workflow.current_step = target.value
            if target is OrderStatus.CANCELLED:
                workflow.status = RunStatus.CANCELLED
            elif target in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}:
                workflow.status = RunStatus.SUCCEEDED
        if target is OrderStatus.CANCELLED:
            self.event_store.publish(
                session,
                create_event(
                    OrderCancelledPayload(order_id=order.id, reason=reason),
                    aggregate_type="order",
                    aggregate_id=order.id,
                    aggregate_version=order.version,
                    correlation_id=workflow.correlation_id if workflow else order.id,
                    workflow_id=workflow.id if workflow else None,
                    idempotency_key=f"order-cancelled:{order.id}",
                ),
            )
        self._audit(
            session, order, "order.status_changed", actor, previous.value, target.value, reason
        )
        session.flush()
        return order

    @staticmethod
    def get(session: Session, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
        statement = select(Order).where(Order.id == order_id)
        if for_update:
            statement = statement.with_for_update()
        order = session.scalar(statement)
        if order is None:
            raise OrderNotFoundError(str(order_id))
        return order

    @staticmethod
    def list(session: Session, *, limit: int = 100):
        return session.scalars(select(Order).order_by(Order.ordered_at.desc()).limit(limit)).all()

    def _reserve(self, session, *, sku, quantity, order, external_line_id, correlation_id):
        candidates = session.scalars(
            select(InventoryItem.id)
            .where(InventoryItem.sku == sku)
            .order_by((InventoryItem.quantity_on_hand - InventoryItem.reserved_quantity).desc())
        ).all()
        for inventory_id in candidates:
            before = session.get(InventoryItem, inventory_id)
            previous_available = before.available_quantity if before else 0
            reserved = session.execute(
                update(InventoryItem)
                .where(
                    InventoryItem.id == inventory_id,
                    InventoryItem.quantity_on_hand - InventoryItem.reserved_quantity >= quantity,
                )
                .values(
                    reserved_quantity=InventoryItem.reserved_quantity + quantity,
                    version=InventoryItem.version + 1,
                )
                .returning(InventoryItem.id)
                .execution_options(synchronize_session=False)
            ).scalar_one_or_none()
            if reserved is None:
                continue
            session.expire_all()
            item = session.get(InventoryItem, inventory_id)
            assert item is not None
            movement = InventoryMovement(
                inventory_item_id=item.id,
                movement_type=InventoryMovementType.RESERVATION,
                quantity_delta=quantity,
                reason="Reserved for marketplace order",
                source_type="order",
                source_id=str(order.id),
                idempotency_key=f"order-reservation:{order.id}:{external_line_id}",
            )
            session.add(movement)
            session.flush()
            self._inventory_events(
                session, item, movement, correlation_id, previous_available, quantity_delta=0
            )
            return item
        raise InsufficientInventoryError(f"Insufficient available inventory for SKU {sku}")

    def _release_order(self, session: Session, order: Order) -> None:
        for line in order.items:
            item = session.get(InventoryItem, line.inventory_item_id)
            assert item is not None
            previous_available = item.available_quantity
            result = session.execute(
                update(InventoryItem)
                .where(
                    InventoryItem.id == item.id,
                    InventoryItem.reserved_quantity >= line.quantity,
                )
                .values(
                    reserved_quantity=InventoryItem.reserved_quantity - line.quantity,
                    version=InventoryItem.version + 1,
                )
                .returning(InventoryItem.id)
                .execution_options(synchronize_session=False)
            ).scalar_one_or_none()
            if result is None:
                raise OrderValidationError("Order reservation is missing")
            session.expire(item)
            movement = self._movement(order, line, InventoryMovementType.RELEASE, -line.quantity)
            session.add(movement)
            session.flush()
            self._inventory_events(session, item, movement, order.id, previous_available, 0)

    def _ship_order(self, session: Session, order: Order) -> None:
        for line in order.items:
            item = session.get(InventoryItem, line.inventory_item_id)
            assert item is not None
            previous_available = item.available_quantity
            result = session.execute(
                update(InventoryItem)
                .where(
                    InventoryItem.id == item.id,
                    InventoryItem.reserved_quantity >= line.quantity,
                    InventoryItem.quantity_on_hand >= line.quantity,
                )
                .values(
                    quantity_on_hand=InventoryItem.quantity_on_hand - line.quantity,
                    reserved_quantity=InventoryItem.reserved_quantity - line.quantity,
                    version=InventoryItem.version + 1,
                )
                .returning(InventoryItem.id)
                .execution_options(synchronize_session=False)
            ).scalar_one_or_none()
            if result is None:
                raise OrderValidationError("Reserved inventory cannot be dispatched")
            session.expire(item)
            movement = self._movement(order, line, InventoryMovementType.SHIPMENT, -line.quantity)
            session.add(movement)
            session.flush()
            self._inventory_events(
                session, item, movement, order.id, previous_available, -line.quantity
            )

    @staticmethod
    def _movement(order, line, movement_type, quantity_delta):
        return InventoryMovement(
            inventory_item_id=line.inventory_item_id,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            reason=f"Order {movement_type.value}",
            source_type="order",
            source_id=str(order.id),
            idempotency_key=f"order-{movement_type.value}:{order.id}:{line.external_line_id}",
        )

    def _inventory_events(
        self, session, item, movement, correlation_id, previous_available, quantity_delta
    ):
        self.event_store.publish(
            session,
            create_event(
                InventoryChangedPayload(
                    inventory_item_id=item.id,
                    quantity_delta=quantity_delta,
                    quantity_on_hand=item.quantity_on_hand,
                    reserved_quantity=item.reserved_quantity,
                ),
                aggregate_type="inventory_item",
                aggregate_id=item.id,
                aggregate_version=item.version,
                correlation_id=correlation_id,
                idempotency_key=f"inventory-changed:{movement.id}",
            ),
        )
        if previous_available > item.low_stock_threshold >= item.available_quantity:
            self.event_store.publish(
                session,
                create_event(
                    LowStockPayload(
                        inventory_item_id=item.id,
                        sku=item.sku,
                        available_quantity=item.available_quantity,
                        threshold=item.low_stock_threshold,
                    ),
                    aggregate_type="inventory_item",
                    aggregate_id=item.id,
                    aggregate_version=item.version,
                    correlation_id=correlation_id,
                    idempotency_key=f"low-stock:{movement.id}",
                ),
            )

    @staticmethod
    def _validate(command: NormalizedMarketplaceOrder) -> None:
        if command.status not in {OrderStatus.PENDING, OrderStatus.PAID}:
            raise OrderValidationError("New orders must be pending or paid")
        line_ids = [line.external_line_id for line in command.items]
        if len(line_ids) != len(set(line_ids)):
            raise OrderValidationError("External line IDs must be unique within an order")
        if any(
            line.discount_amount > line.unit_price * line.quantity + line.tax_amount
            for line in command.items
        ):
            raise OrderValidationError("Line discount cannot exceed the line value")

    @staticmethod
    def _payload_hash(command: NormalizedMarketplaceOrder) -> str:
        business_payload = command.model_dump(mode="json", exclude={"source_event_id"})
        encoded = json.dumps(business_payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _audit(session, order, action, actor, before, after, reason="Marketplace order ingestion"):
        session.add(
            AuditEvent(
                actor_type="system" if actor == "marketplace" else "user",
                actor_id=actor,
                action=action,
                resource_type="order",
                resource_id=order.id,
                before_state={"status": before} if before else None,
                after_state={"status": after},
                reason=reason,
                correlation_id=order.workflow_run.correlation_id,
            )
        )
