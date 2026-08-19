import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.config import Settings
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import (
    DomainEvent,
    InventoryItem,
    InventoryMovement,
    PurchaseOrder,
)


@pytest.fixture
def inventory_environment(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'inventory.sqlite'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    application = create_app(Settings(environment="test", _env_file=None))

    def session_dependency():
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = session_dependency
    with TestClient(application) as client:
        yield client, factory
    engine.dispose()


def prepare_shipped_purchase_order(client: TestClient, payload: dict) -> tuple[str, str]:
    ingested = client.post(
        "/api/v1/approved-products",
        json=payload,
        headers={"Idempotency-Key": "approved-product:inventory-test"},
    )
    assert ingested.status_code == 202
    procurement_id = ingested.json()["procurement_request_id"]
    submitted = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/submit-for-approval",
        json={"requester": "buyer@example.com", "reason": "Quote reviewed"},
    )
    approval_id = submitted.json()["approval_id"]
    approved = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={"approver": "finance@example.com", "reason": "Budget approved"},
    )
    assert approved.status_code == 200
    ordered = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/order",
        json={"actor": "buyer@example.com", "reason": "Submitted to supplier"},
    )
    assert ordered.status_code == 200
    shipped = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/mark-shipped",
        json={"actor": "buyer@example.com", "reason": "Supplier dispatched"},
    )
    assert shipped.status_code == 200
    return procurement_id, shipped.json()["purchase_order"]["id"]


def receive(
    client: TestClient,
    purchase_order_id: str,
    *,
    quantity: int,
    cost: str,
    key: str,
):
    return client.post(
        f"/api/v1/purchase-orders/{purchase_order_id}/receive",
        headers={"Idempotency-Key": key},
        json={
            "sku": "BOTTLE-BLACK-750",
            "storage_location": "warehouse-a",
            "quantity_received": quantity,
            "landed_unit_cost": cost,
            "low_stock_threshold": 30,
            "actor": "warehouse@example.com",
            "reason": "Counted against delivery note",
        },
    )


def test_partial_receipts_adjustments_history_and_low_stock(
    inventory_environment, approved_product_payload
) -> None:
    client, factory = inventory_environment
    procurement_id, purchase_order_id = prepare_shipped_purchase_order(
        client, approved_product_payload
    )

    first = receive(client, purchase_order_id, quantity=40, cost="5.00", key="receipt:1")
    duplicate = receive(client, purchase_order_id, quantity=40, cost="5.00", key="receipt:1")
    second = receive(client, purchase_order_id, quantity=60, cost="7.00", key="receipt:2")

    assert first.status_code == duplicate.status_code == second.status_code == 200
    assert first.json()["inventory"]["quantity_on_hand"] == 40
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["inventory"]["quantity_on_hand"] == 40
    assert second.json()["inventory"]["quantity_on_hand"] == 100
    assert second.json()["inventory"]["cost_basis"] == "6.2000"
    inventory_id = second.json()["inventory"]["id"]

    procurement = client.get(f"/api/v1/procurement-requests/{procurement_id}")
    assert procurement.json()["status"] == "received"
    lookup = client.get("/api/v1/inventory/BOTTLE-BLACK-750")
    assert lookup.status_code == 200
    assert lookup.json()["count"] == 1
    assert lookup.json()["items"][0]["available_quantity"] == 100

    adjustment = client.post(
        f"/api/v1/inventory/items/{inventory_id}/adjustments",
        headers={"Idempotency-Key": "adjustment:cycle-count"},
        json={
            "quantity_delta": -70,
            "actor": "warehouse@example.com",
            "reason": "Cycle count correction",
        },
    )
    duplicate_adjustment = client.post(
        f"/api/v1/inventory/items/{inventory_id}/adjustments",
        headers={"Idempotency-Key": "adjustment:cycle-count"},
        json={
            "quantity_delta": -70,
            "actor": "warehouse@example.com",
            "reason": "Cycle count correction",
        },
    )
    assert adjustment.status_code == duplicate_adjustment.status_code == 200
    assert adjustment.json()["inventory"]["available_quantity"] == 30
    assert duplicate_adjustment.json()["duplicate"] is True

    negative = client.post(
        f"/api/v1/inventory/items/{inventory_id}/adjustments",
        headers={"Idempotency-Key": "adjustment:invalid-negative"},
        json={
            "quantity_delta": -31,
            "actor": "warehouse@example.com",
            "reason": "Invalid correction",
        },
    )
    assert negative.status_code == 409

    history = client.get(f"/api/v1/inventory/items/{inventory_id}/movements")
    assert history.status_code == 200
    assert history.json()["count"] == 3

    with factory() as session:
        purchase_order = session.get(PurchaseOrder, uuid.UUID(purchase_order_id))
        assert purchase_order is not None and purchase_order.received_quantity == 100
        assert session.scalar(select(func.count()).select_from(InventoryItem)) == 1
        assert session.scalar(select(func.count()).select_from(InventoryMovement)) == 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "STOCK_RECEIVED")
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "INVENTORY_CHANGED")
            )
            == 3
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "LOW_STOCK")
            )
            == 1
        )


def test_over_receipt_rolls_back_without_extra_stock(
    inventory_environment, approved_product_payload
) -> None:
    client, factory = inventory_environment
    _, purchase_order_id = prepare_shipped_purchase_order(client, approved_product_payload)
    first = receive(client, purchase_order_id, quantity=40, cost="5.00", key="receipt:1")
    over_receipt = receive(
        client, purchase_order_id, quantity=61, cost="5.00", key="receipt:too-many"
    )

    assert first.status_code == 200
    assert over_receipt.status_code == 409
    with factory() as session:
        item = session.scalar(select(InventoryItem))
        assert item is not None and item.quantity_on_hand == 40
        assert session.scalar(select(func.count()).select_from(InventoryMovement)) == 1


def test_adjustment_cannot_reduce_stock_below_reservations(
    inventory_environment, approved_product_payload
) -> None:
    client, factory = inventory_environment
    _, purchase_order_id = prepare_shipped_purchase_order(client, approved_product_payload)
    receipt = receive(client, purchase_order_id, quantity=100, cost="5.00", key="receipt:full")
    inventory_id = receipt.json()["inventory"]["id"]
    with factory.begin() as session:
        item = session.get(InventoryItem, uuid.UUID(inventory_id))
        assert item is not None
        item.reserved_quantity = 95

    response = client.post(
        f"/api/v1/inventory/items/{inventory_id}/adjustments",
        headers={"Idempotency-Key": "adjustment:below-reserved"},
        json={
            "quantity_delta": -10,
            "actor": "warehouse@example.com",
            "reason": "Would consume reserved stock",
        },
    )

    assert response.status_code == 409
    with factory() as session:
        item = session.get(InventoryItem, uuid.UUID(inventory_id))
        assert item is not None
        assert item.quantity_on_hand == 100
        assert item.reserved_quantity == 95
