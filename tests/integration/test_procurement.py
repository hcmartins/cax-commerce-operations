import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.config import Settings
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import (
    AuditEvent,
    DomainEvent,
    ProcurementRequest,
    PurchaseOrder,
)


@pytest.fixture
def procurement_environment(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'procurement.sqlite'}")
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


def ingest(client: TestClient, payload: dict) -> str:
    response = client.post(
        "/api/v1/approved-products",
        json=payload,
        headers={"Idempotency-Key": "approved-product:intel-product-123"},
    )
    assert response.status_code == 202
    return response.json()["procurement_request_id"]


def command(actor: str = "operator@example.com", reason: str = "Confirmed") -> dict:
    return {"actor": actor, "reason": reason}


def test_complete_procurement_state_machine(
    procurement_environment, approved_product_payload
) -> None:
    client, factory = procurement_environment
    procurement_id = ingest(client, approved_product_payload)

    review = client.get(f"/api/v1/procurement-requests/{procurement_id}")
    assert review.status_code == 200
    assert review.json()["status"] == "proposed"
    assert review.json()["quote"]["moq"] == 100
    assert review.json()["quote"]["unit_cost"] == "4.2000"

    submitted = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/submit-for-approval",
        json={
            "requester": "buyer@example.com",
            "reason": "Supplier and quote reviewed",
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["procurement"]["status"] == "awaiting_approval"
    approval_id = submitted.json()["approval_id"]

    approved = client.post(
        f"/api/v1/approvals/{approval_id}/approve",
        json={"approver": "finance@example.com", "reason": "Budget approved"},
    )
    assert approved.status_code == 200

    after_approval = client.get(f"/api/v1/procurement-requests/{procurement_id}")
    assert after_approval.json()["status"] == "approved"
    assert after_approval.json()["purchase_order"]["status"] == "approved"
    assert after_approval.json()["purchase_order"]["total_amount"] == "500.0000"

    ordered = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/order",
        json=command() | {"external_reference": "SUPPLIER-PO-9"},
    )
    assert ordered.status_code == 200
    assert ordered.json()["status"] == "ordered"

    expected_arrival = datetime.now(UTC) + timedelta(days=14)
    shipped = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/mark-shipped",
        json=command(reason="Supplier confirmed dispatch")
        | {"expected_arrival_at": expected_arrival.isoformat()},
    )
    assert shipped.status_code == 200
    assert shipped.json()["status"] == "shipped"

    purchase_order_id = shipped.json()["purchase_order"]["id"]
    received = client.post(
        f"/api/v1/purchase-orders/{purchase_order_id}/receive",
        headers={"Idempotency-Key": "receipt:complete-procurement"},
        json={
            "sku": "BOTTLE-BLACK-750",
            "storage_location": "warehouse-a",
            "quantity_received": 100,
            "landed_unit_cost": "5.00",
            "low_stock_threshold": 10,
            "actor": "warehouse@example.com",
            "reason": "Goods physically received",
        },
    )
    assert received.status_code == 200
    assert received.json()["inventory"]["quantity_on_hand"] == 100
    final_review = client.get(f"/api/v1/procurement-requests/{procurement_id}")
    assert final_review.json()["status"] == "received"

    invalid_cancel = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/cancel",
        json=command(reason="Too late"),
    )
    assert invalid_cancel.status_code == 409

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PurchaseOrder)) == 1
        transition_audits = session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "procurement.status_changed")
        )
        assert transition_audits == 5
        status_events = session.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.event_type == "PROCUREMENT_STATUS_CHANGED")
        )
        assert status_events == 5
        event_types = set(session.scalars(select(DomainEvent.event_type)))
        assert {
            "PRODUCT_APPROVED",
            "PROCUREMENT_REQUESTED",
            "PURCHASE_APPROVED",
            "PURCHASE_ORDER_CREATED",
        } <= event_types


def test_proposed_procurement_can_be_cancelled(
    procurement_environment, approved_product_payload
) -> None:
    client, factory = procurement_environment
    procurement_id = ingest(client, approved_product_payload)

    cancelled = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/cancel",
        json=command(reason="Opportunity withdrawn"),
    )

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    with factory() as session:
        request = session.get(ProcurementRequest, uuid.UUID(cancelled.json()["id"]))
        assert request is not None and request.status.value == "cancelled"
