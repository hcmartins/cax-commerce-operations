import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.config import Settings
from commerce_operations.events import DatabaseEventStore, create_event
from commerce_operations.events.types import LowStockPayload, StockReceivedPayload
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import EventStatus
from commerce_operations.persistence.models import (
    DomainEvent,
    InventoryItem,
    Product,
    ReorderRecommendation,
)
from commerce_operations.worker import Worker


def test_worker_processes_supported_events_and_leaves_unconfigured_specialists_pending(
    tmp_path,
):
    database = tmp_path / "worker.sqlite"
    heartbeat = tmp_path / "worker-heartbeat"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id="worker-product",
            source_workflow_run_id="source-run",
            source_recommendation_id="recommendation",
            source_payload_hash="0" * 64,
            name="Worker product",
        )
        session.add(product)
        session.flush()
        inventory = InventoryItem(
            product_id=product.id,
            sku="WORKER-SKU",
            storage_location="warehouse",
            quantity_on_hand=2,
            reserved_quantity=0,
            cost_basis="5",
            currency="GBP",
            low_stock_threshold=5,
        )
        session.add(inventory)
        session.flush()
        store = DatabaseEventStore()
        low_stock = store.publish(
            session,
            create_event(
                LowStockPayload(
                    inventory_item_id=inventory.id,
                    sku=inventory.sku,
                    available_quantity=2,
                    threshold=5,
                ),
                aggregate_type="inventory_item",
                aggregate_id=inventory.id,
                aggregate_version=1,
                idempotency_key="worker-low-stock",
            ),
        )
        stock_received = store.publish(
            session,
            create_event(
                StockReceivedPayload(
                    inventory_item_id=inventory.id,
                    purchase_order_id=uuid.uuid4(),
                    quantity=2,
                ),
                aggregate_type="inventory_item",
                aggregate_id=inventory.id,
                aggregate_version=2,
                idempotency_key="worker-stock-received",
            ),
        )
        low_stock_id, stock_received_id = low_stock.id, stock_received.id

    settings = Settings(
        environment="test",
        database_url=f"sqlite+pysqlite:///{database}",
        worker_heartbeat_path=str(heartbeat),
        worker_batch_size=1,
        _env_file=None,
    )
    assert Worker(settings, factory).run_once() == 1

    with factory() as session:
        assert session.get(DomainEvent, low_stock_id).publication_status is EventStatus.PUBLISHED
        assert session.get(DomainEvent, stock_received_id).publication_status is EventStatus.PENDING
        assert session.scalar(select(ReorderRecommendation)) is not None
        assert heartbeat.exists()
    engine.dispose()
