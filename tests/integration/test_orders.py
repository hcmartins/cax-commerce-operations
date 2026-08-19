from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.application.orders import InsufficientInventoryError, OrderService
from commerce_operations.integrations.marketplaces.orders import (
    NormalizedMarketplaceOrder,
    NormalizedOrderItem,
)
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import InventoryMovementType, OrderStatus
from commerce_operations.persistence.models import (
    DomainEvent,
    InventoryItem,
    InventoryMovement,
    Order,
    Product,
    WorkflowRun,
)


@pytest.fixture
def order_database(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'orders.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id="order-product",
            source_workflow_run_id="source-workflow",
            source_recommendation_id="recommendation",
            source_payload_hash="product-hash",
            name="Bottle",
        )
        session.add(product)
        session.flush()
        inventory = InventoryItem(
            product_id=product.id,
            sku="BOTTLE-1",
            storage_location="warehouse",
            quantity_on_hand=5,
            reserved_quantity=0,
            cost_basis=Decimal("5"),
            currency="GBP",
            low_stock_threshold=1,
        )
        session.add(inventory)
        session.flush()
        inventory_id = inventory.id
    yield factory, inventory_id
    engine.dispose()


def order(external_order_id: str, source_event_id: str, quantity: int = 3):
    return NormalizedMarketplaceOrder(
        marketplace="ebay",
        marketplace_account_id="seller-1",
        external_order_id=external_order_id,
        source_event_id=source_event_id,
        status=OrderStatus.PAID,
        currency="GBP",
        total_amount=Decimal("44.97"),
        customer_reference="customer-token",
        shipping_details={"country": "GB"},
        ordered_at=datetime.now(UTC),
        items=(
            NormalizedOrderItem(
                external_line_id="line-1",
                sku="BOTTLE-1",
                quantity=quantity,
                unit_price=Decimal("14.99"),
            ),
        ),
    )


def test_duplicate_webhook_does_not_duplicate_order_or_reservation(order_database):
    factory, inventory_id = order_database
    service = OrderService()
    command = order("order-1", "event-1")
    with factory.begin() as session:
        first = service.ingest(session, command)
    redelivery = command.model_copy(update={"source_event_id": "event-2"})
    with factory.begin() as session:
        duplicate = service.ingest(session, redelivery)
        assert duplicate.duplicate is True
        assert duplicate.order.id == first.order.id
    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        assert inventory is not None
        assert inventory.reserved_quantity == 3
        assert inventory.available_quantity == 2
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(InventoryMovement)
                .where(InventoryMovement.movement_type == InventoryMovementType.RESERVATION)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "ORDER_RECEIVED")
            )
            == 1
        )


def test_insufficient_inventory_rolls_back_order_and_reservation(order_database):
    factory, inventory_id = order_database
    with pytest.raises(InsufficientInventoryError), factory.begin() as session:
        OrderService().ingest(session, order("too-large", "event-large", quantity=6))
    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        assert inventory is not None and inventory.reserved_quantity == 0
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 0


def test_cancellation_releases_and_dispatch_consumes_reserved_inventory(order_database):
    factory, inventory_id = order_database
    service = OrderService()
    with factory.begin() as session:
        first = service.ingest(session, order("cancel-me", "event-cancel", quantity=2)).order
        first_id = first.id
    with factory.begin() as session:
        service.update_status(
            session,
            first_id,
            OrderStatus.CANCELLED,
            actor="operator@example.com",
            reason="Buyer cancellation",
        )
    with factory.begin() as session:
        second = service.ingest(session, order("dispatch-me", "event-dispatch", quantity=3)).order
        second_id = second.id
        service.update_status(
            session, second_id, OrderStatus.PROCESSING, actor="operator@example.com", reason="Pack"
        )
        service.update_status(
            session,
            second_id,
            OrderStatus.DISPATCHED,
            actor="operator@example.com",
            reason="Carrier handoff",
        )
    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        assert inventory is not None
        assert inventory.quantity_on_hand == 2
        assert inventory.reserved_quantity == 0
        movements = list(
            session.scalars(
                select(InventoryMovement.movement_type).order_by(InventoryMovement.created_at)
            )
        )
        assert movements == [
            InventoryMovementType.RESERVATION,
            InventoryMovementType.RELEASE,
            InventoryMovementType.RESERVATION,
            InventoryMovementType.SHIPMENT,
        ]


def test_concurrent_orders_cannot_oversell(order_database):
    factory, inventory_id = order_database
    barrier = Barrier(2)

    def attempt(command):
        barrier.wait()
        try:
            with factory.begin() as session:
                return OrderService().ingest(session, command).order.external_order_id
        except InsufficientInventoryError:
            return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                attempt,
                [
                    order("concurrent-1", "concurrent-event-1"),
                    order("concurrent-2", "concurrent-event-2"),
                ],
            )
        )
    assert results.count("insufficient") == 1
    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        assert inventory is not None
        assert inventory.reserved_quantity == 3
        assert inventory.available_quantity == 2
        assert session.scalar(select(func.count()).select_from(Order)) == 1
