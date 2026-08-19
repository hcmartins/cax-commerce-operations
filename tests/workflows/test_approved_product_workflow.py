"""End-to-end workflow tests for the approved-product to procurement journey.

Unlike tests/integration/test_workflow_engine.py (unit-level WorkflowEngine
behaviour) and tests/integration/test_orchestration.py (low-stock and
stock-received routes), this module drives the exact pipeline the production
worker runs: a real API request publishes a domain event, then the same
event-type set and handler registry the worker registers
(`commerce_operations.worker.CORE_EVENT_TYPES`) processes it, and the
resulting orchestrator-owned WorkflowRun is asserted end to end.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.application.orchestration import AutonomousOrchestrator
from commerce_operations.config import Settings
from commerce_operations.events import DatabaseEventStore, EventHandlerRegistry, EventProcessor
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.enums import RunStatus
from commerce_operations.persistence.models import WorkflowRun
from commerce_operations.worker import CORE_EVENT_TYPES


@pytest.fixture
def workflow_runtime(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workflow.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(environment="test", _env_file=None)
    application = create_app(settings)

    def session_dependency():
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = session_dependency

    registry = EventHandlerRegistry()
    AutonomousOrchestrator(settings=settings).register(registry, CORE_EVENT_TYPES)
    processor = EventProcessor(factory, registry)

    with TestClient(application) as client:
        yield client, factory, processor
    engine.dispose()


def run_worker_batch(processor: EventProcessor, factory) -> list[uuid.UUID]:
    """Process every pending core-workflow event, exactly as Worker.run_once does."""
    with factory() as session:
        pending = DatabaseEventStore().pending(session, limit=100, event_types=CORE_EVENT_TYPES)
        event_ids = [record.id for record in pending]
    for event_id in event_ids:
        processor.process(event_id)
    return event_ids


def test_approved_product_workflow_completes_through_the_worker_pipeline(
    workflow_runtime, approved_product_payload
) -> None:
    client, factory, processor = workflow_runtime

    ingested = client.post(
        "/api/v1/approved-products",
        json=approved_product_payload,
        headers={"Idempotency-Key": "approved-product:workflow-test"},
    )
    assert ingested.status_code == 202
    procurement_request_id = ingested.json()["procurement_request_id"]

    processed_events = run_worker_batch(processor, factory)
    assert len(processed_events) == 1

    with factory() as session:
        orchestrated = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.workflow_name == "orchestrator:product-approved-to-procurement"
            )
        )
        assert orchestrated is not None
        assert orchestrated.status is RunStatus.SUCCEEDED
        assert orchestrated.current_step == "procurement_proposed"
        assert orchestrated.checkpoints["procurement_request_id"] == procurement_request_id

    review = client.get(f"/api/v1/procurement-requests/{procurement_request_id}")
    assert review.status_code == 200
    assert review.json()["status"] == "proposed"


def test_reprocessing_pending_events_after_a_worker_restart_is_idempotent(
    workflow_runtime, approved_product_payload
) -> None:
    client, factory, processor = workflow_runtime

    ingested = client.post(
        "/api/v1/approved-products",
        json=approved_product_payload,
        headers={"Idempotency-Key": "approved-product:workflow-restart-test"},
    )
    assert ingested.status_code == 202

    first_pass = run_worker_batch(processor, factory)
    assert len(first_pass) == 1
    # A worker restarting before the receipt commits (or simply polling again)
    # must not create a second orchestrated workflow run or duplicate audit state.
    second_pass = run_worker_batch(processor, factory)
    assert second_pass == []

    with factory() as session:
        orchestrated_runs = session.scalar(
            select(func.count())
            .select_from(WorkflowRun)
            .where(WorkflowRun.workflow_name == "orchestrator:product-approved-to-procurement")
        )
        assert orchestrated_runs == 1
