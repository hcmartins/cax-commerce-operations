import uuid

import pytest
from pydantic import ValidationError

from commerce_operations.events.types import (
    EVENT_TYPES_BY_PAYLOAD,
    PAYLOAD_TYPES,
    EventType,
    ProductApprovedPayload,
    create_event,
)


def test_all_supported_events_have_one_typed_payload() -> None:
    assert set(PAYLOAD_TYPES) == set(EventType)
    assert len(EVENT_TYPES_BY_PAYLOAD) == len(EventType)


def test_create_event_populates_trace_fields() -> None:
    aggregate_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    causation_id = uuid.uuid4()
    payload = ProductApprovedPayload(
        product_id=aggregate_id,
        source_product_id="source-product-1",
    )

    event = create_event(
        payload,
        aggregate_type="product",
        aggregate_id=aggregate_id,
        aggregate_version=1,
        workflow_id=workflow_id,
        causation_id=causation_id,
        idempotency_key="product-approved:source-product-1",
    )

    assert event.event_type is EventType.PRODUCT_APPROVED
    assert event.event_id
    assert event.correlation_id
    assert event.workflow_id == workflow_id
    assert event.causation_id == causation_id
    assert event.occurred_at.tzinfo is not None


def test_payloads_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ProductApprovedPayload(
            product_id=uuid.uuid4(),
            source_product_id="source-product-1",
            unexpected=True,
        )
