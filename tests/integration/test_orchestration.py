import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.application.orchestration import AutonomousOrchestrator
from commerce_operations.config import Settings
from commerce_operations.events import DatabaseEventStore, create_event
from commerce_operations.events.handlers import EventHandlerRegistry
from commerce_operations.events.processor import EventProcessor
from commerce_operations.events.types import LowStockPayload, StockReceivedPayload
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import RunStatus
from commerce_operations.persistence.models import (
    DomainEvent,
    InventoryItem,
    Product,
    ReorderRecommendation,
    WorkflowRun,
)


def setup_runtime(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'orchestration.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    registry = EventHandlerRegistry()
    orchestrator = AutonomousOrchestrator(settings=Settings(environment="test", _env_file=None))
    orchestrator.register(registry)
    return engine, factory, registry, orchestrator


def add_inventory(factory, *, available=2, threshold=5):
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id=str(uuid.uuid4()),
            source_workflow_run_id="source-run",
            source_recommendation_id="recommendation",
            source_payload_hash="0" * 64,
            name="Workflow product",
        )
        session.add(product)
        session.flush()
        inventory = InventoryItem(
            product_id=product.id,
            sku=f"SKU-{uuid.uuid4()}",
            storage_location="warehouse-a",
            quantity_on_hand=available,
            reserved_quantity=0,
            cost_basis="5.00",
            currency="GBP",
            low_stock_threshold=threshold,
        )
        session.add(inventory)
        session.flush()
        return inventory.id, inventory.sku


def publish(factory, payload, key):
    with factory.begin() as session:
        event = DatabaseEventStore().publish(
            session,
            create_event(
                payload,
                aggregate_type="inventory_item",
                aggregate_id=payload.inventory_item_id,
                aggregate_version=1,
                idempotency_key=key,
            ),
        )
        return event.id


def test_low_stock_creates_one_idempotent_reorder_recommendation(tmp_path):
    engine, factory, registry, _ = setup_runtime(tmp_path)
    inventory_id, sku = add_inventory(factory)
    event_id = publish(
        factory,
        LowStockPayload(
            inventory_item_id=inventory_id,
            sku=sku,
            available_quantity=2,
            threshold=5,
        ),
        "low-stock:test",
    )
    processor = EventProcessor(factory, registry)

    first = processor.process(event_id)
    duplicate = processor.process(event_id)

    assert first.handlers_processed == 1
    assert duplicate.handlers_skipped == 1
    with factory() as session:
        recommendation = session.scalar(select(ReorderRecommendation))
        assert recommendation is not None
        assert recommendation.suggested_quantity == 8
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 1
    engine.dispose()


def test_missing_listing_agent_fails_safely_and_emits_failure(tmp_path):
    engine, factory, registry, _ = setup_runtime(tmp_path)
    inventory_id, _ = add_inventory(factory, available=10)
    event_id = publish(
        factory,
        StockReceivedPayload(
            purchase_order_id=uuid.uuid4(),
            inventory_item_id=inventory_id,
            quantity=10,
        ),
        "stock-received:no-agent",
    )

    EventProcessor(factory, registry).process(event_id)

    with factory() as session:
        workflow = session.scalar(select(WorkflowRun))
        assert workflow is not None
        assert workflow.status is RunStatus.FAILED
        assert "No marketplace listing agents" in workflow.error["message"]
        failures = session.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.event_type == "WORKFLOW_FAILED")
        )
        assert failures == 1
    engine.dispose()
