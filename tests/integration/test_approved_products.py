from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.config import Settings
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import (
    DomainEvent,
    ProcurementRequest,
    Product,
    Supplier,
    SupplierQuote,
    WorkflowRun,
)


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'intelligence.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def api_client(session_factory) -> TestClient:
    application = create_app(Settings(environment="test", _env_file=None))

    def session_dependency():
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = session_dependency
    with TestClient(application) as client:
        yield client


def post_approved_product(client: TestClient, payload: dict):
    return client.post(
        "/api/v1/approved-products",
        json=payload,
        headers={"Idempotency-Key": "approved-product:intel-product-123"},
    )


def test_ingestion_persists_snapshot_workflow_and_event(
    api_client, session_factory, approved_product_payload
) -> None:
    response = post_approved_product(api_client, approved_product_payload)

    assert response.status_code == 202
    body = response.json()
    assert body["duplicate"] is False
    assert body["procurement_status"] == "proposed"

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 1
        assert session.scalar(select(func.count()).select_from(Supplier)) == 1
        assert session.scalar(select(func.count()).select_from(SupplierQuote)) == 1
        assert session.scalar(select(func.count()).select_from(ProcurementRequest)) == 1
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1
        quote = session.scalar(select(SupplierQuote))
        event = session.scalar(select(DomainEvent))
        assert quote is not None and quote.currency == "GBP"
        assert event is not None and event.event_type == "PRODUCT_APPROVED"
        assert event.workflow_id is not None
        assert event.payload["source_workflow_run_id"] == "intel-run-456"


def test_exact_retry_returns_original_resources_without_duplicates(
    api_client, session_factory, approved_product_payload
) -> None:
    first = post_approved_product(api_client, approved_product_payload)
    second = post_approved_product(api_client, approved_product_payload)

    assert first.status_code == second.status_code == 202
    assert second.json()["duplicate"] is True
    for key in ("product_id", "procurement_request_id", "workflow_run_id", "event_id"):
        assert second.json()[key] == first.json()[key]

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Product)) == 1
        assert session.scalar(select(func.count()).select_from(ProcurementRequest)) == 1
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1


def test_conflicting_retry_is_rejected(api_client, approved_product_payload) -> None:
    first = post_approved_product(api_client, approved_product_payload)
    changed = deepcopy(approved_product_payload)
    changed["economics"]["recommended_selling_price"] = "19.99"
    conflict = post_approved_product(api_client, changed)

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert "different approved data" in conflict.json()["detail"]


def test_invalid_contract_is_rejected(api_client, approved_product_payload) -> None:
    invalid = deepcopy(approved_product_payload)
    invalid["recommendation"]["evidence"] = []

    response = post_approved_product(api_client, invalid)

    assert response.status_code == 422
