"""Contract tests for the public, versioned HTTP surface.

These tests protect the wire contract itself: the checked-in versioned example
payload, the shape of the OpenAPI document, and strict rejection of unknown
fields. They intentionally do not re-check business side effects (see
tests/integration and tests/workflows for that); a break here means an
external caller's request or response would stop matching what is published.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from commerce_operations.config import Settings
from commerce_operations.integrations.repository_1.contracts import ApprovedProductRequestV1
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session

CONTRACT_EXAMPLE = (
    Path(__file__).parents[2]
    / "contracts"
    / "repository-1"
    / "v1"
    / "approved-product.example.json"
)


@pytest.fixture
def contract_client(tmp_path) -> TestClient:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'contract.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    application = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://test:test@localhost:5432/test",
            _env_file=None,
        )
    )

    def session_dependency():
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = session_dependency
    with TestClient(application) as client:
        yield client
    engine.dispose()


def test_versioned_example_payload_matches_the_published_request_schema() -> None:
    """The example published for integration partners must stay parseable."""
    raw = json.loads(CONTRACT_EXAMPLE.read_text())

    request = ApprovedProductRequestV1.model_validate(raw)

    assert request.schema_version == 1
    assert request.source_system == "commerce-intelligence"


def test_openapi_document_publishes_the_versioned_routes(contract_client: TestClient) -> None:
    response = contract_client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    paths = document["paths"]
    assert "/api/v1/approved-products" in paths
    assert "/api/v1/procurement-requests/{procurement_id}" in paths
    assert "/health" in paths
    assert "/ready" in paths
    # Every business route stays under the versioned prefix; only the
    # unversioned health/docs surface is exempt.
    unversioned = {
        path
        for path in paths
        if not path.startswith("/api/v1") and path not in {"/health", "/ready", "/openapi.json"}
    }
    assert unversioned == set()


def test_docs_ui_is_available_outside_production(contract_client: TestClient) -> None:
    response = contract_client.get("/docs")

    assert response.status_code == 200


def test_request_schema_rejects_unknown_fields(
    contract_client: TestClient, approved_product_payload: dict
) -> None:
    payload = approved_product_payload | {"unexpected_field": "should be rejected"}

    response = contract_client.post(
        "/api/v1/approved-products",
        json=payload,
        headers={"Idempotency-Key": "approved-product:contract-test"},
    )

    assert response.status_code == 422


def test_ingestion_response_matches_the_published_response_schema(
    contract_client: TestClient, approved_product_payload: dict
) -> None:
    response = contract_client.post(
        "/api/v1/approved-products",
        json=approved_product_payload,
        headers={"Idempotency-Key": "approved-product:contract-test"},
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body.keys()) == {
        "schema_version",
        "product_id",
        "procurement_request_id",
        "workflow_run_id",
        "event_id",
        "correlation_id",
        "procurement_status",
        "duplicate",
    }


def test_health_and_ready_are_unauthenticated_and_unversioned(contract_client: TestClient) -> None:
    # /health never touches the database; /ready does and may legitimately
    # report 503 here since no real database is wired into this fixture. The
    # contract under test is that both stay reachable without auth and return
    # the published HealthResponse shape either way (see tests/unit/test_api.py
    # for /ready's database-backed success and failure behaviour).
    health = contract_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = contract_client.get("/ready")
    assert ready.status_code in (200, 503)
    if ready.status_code == 200:
        assert ready.json()["status"] == "ready"
