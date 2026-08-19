import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.events import (
    DatabaseEventStore,
    EventHandlerRegistry,
    EventProcessor,
    EventType,
    create_event,
)
from commerce_operations.events.processor import EventProcessingError
from commerce_operations.events.store import EventConflictError
from commerce_operations.events.types import ProductApprovedPayload
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import EventStatus
from commerce_operations.persistence.models import AuditEvent, DomainEvent, EventHandlerReceipt


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'events.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def product_approved_event(*, idempotency_key: str = "product-approved:source-1"):
    product_id = uuid.uuid4()
    return create_event(
        ProductApprovedPayload(product_id=product_id, source_product_id="source-1"),
        aggregate_type="product",
        aggregate_id=product_id,
        aggregate_version=1,
        workflow_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
    )


def audit_handler(event, session: Session) -> None:
    session.add(
        AuditEvent(
            actor_type="event_handler",
            actor_id="tests.product-approved-audit",
            action="business_action_created",
            resource_type=event.aggregate_type,
            resource_id=event.aggregate_id,
            correlation_id=event.correlation_id,
        )
    )


def test_duplicate_events_do_not_duplicate_business_actions(session_factory) -> None:
    store = DatabaseEventStore()
    first_event = product_approved_event()
    duplicate_event = product_approved_event(idempotency_key=first_event.idempotency_key)
    duplicate_event = duplicate_event.model_copy(
        update={"data": first_event.data, "aggregate_id": first_event.aggregate_id}
    )

    with session_factory.begin() as session:
        first_record = store.publish(session, first_event)
        duplicate_record = store.publish(session, duplicate_event)
        assert duplicate_record.id == first_record.id

    registry = EventHandlerRegistry()
    registry.register(EventType.PRODUCT_APPROVED, "tests.audit-product", audit_handler)
    processor = EventProcessor(session_factory, registry, store)

    first_result = processor.process(first_record.id)
    duplicate_result = processor.process(first_record.id)

    assert first_result.handlers_processed == 1
    assert duplicate_result.handlers_processed == 0
    assert duplicate_result.handlers_skipped == 1
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DomainEvent)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert session.scalar(select(func.count()).select_from(EventHandlerReceipt)) == 1


def test_reused_idempotency_key_with_different_payload_is_rejected(session_factory) -> None:
    store = DatabaseEventStore()
    first_event = product_approved_event()
    conflicting_event = product_approved_event(idempotency_key=first_event.idempotency_key)
    with session_factory.begin() as session:
        store.publish(session, first_event)

    with pytest.raises(EventConflictError), session_factory.begin() as session:
        store.publish(session, conflicting_event)


def test_failed_handler_rolls_back_and_can_be_retried(session_factory) -> None:
    store = DatabaseEventStore()
    event = product_approved_event()
    with session_factory.begin() as session:
        record = store.publish(session, event)

    attempts = 0

    def fail_once(event, session: Session) -> None:
        nonlocal attempts
        audit_handler(event, session)
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")

    registry = EventHandlerRegistry()
    registry.register(EventType.PRODUCT_APPROVED, "tests.retryable", fail_once)
    processor = EventProcessor(session_factory, registry, store)

    with pytest.raises(EventProcessingError):
        processor.process(record.id)
    result = processor.process(record.id)

    assert result.handlers_processed == 1
    with session_factory() as session:
        persisted = session.get(DomainEvent, record.id)
        assert persisted is not None
        assert persisted.publication_status is EventStatus.PUBLISHED
        assert persisted.processing_attempts == 2
        assert persisted.last_error is None
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 1
