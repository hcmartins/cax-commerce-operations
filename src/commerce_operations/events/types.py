import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    PRODUCT_APPROVED = "PRODUCT_APPROVED"
    PROCUREMENT_REQUESTED = "PROCUREMENT_REQUESTED"
    PROCUREMENT_STATUS_CHANGED = "PROCUREMENT_STATUS_CHANGED"
    PURCHASE_APPROVED = "PURCHASE_APPROVED"
    PURCHASE_ORDER_CREATED = "PURCHASE_ORDER_CREATED"
    STOCK_RECEIVED = "STOCK_RECEIVED"
    INVENTORY_CHANGED = "INVENTORY_CHANGED"
    LISTING_DRAFT_CREATED = "LISTING_DRAFT_CREATED"
    LISTING_VALIDATION_FAILED = "LISTING_VALIDATION_FAILED"
    LISTING_APPROVED = "LISTING_APPROVED"
    LISTING_PUBLISHED = "LISTING_PUBLISHED"
    ORDER_RECEIVED = "ORDER_RECEIVED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_RETURNED = "ORDER_RETURNED"
    LOW_STOCK = "LOW_STOCK"
    CUSTOMER_MESSAGE_RECEIVED = "CUSTOMER_MESSAGE_RECEIVED"
    REFUND_REQUESTED = "REFUND_REQUESTED"
    WORKFLOW_FAILED = "WORKFLOW_FAILED"


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductApprovedPayload(EventPayload):
    product_id: uuid.UUID
    source_product_id: str
    source_workflow_run_id: str | None = None
    procurement_request_id: uuid.UUID | None = None


class ProcurementRequestedPayload(EventPayload):
    procurement_request_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0)]


class ProcurementStatusChangedPayload(EventPayload):
    procurement_request_id: uuid.UUID
    previous_status: str
    current_status: str
    actor: str


class PurchaseApprovedPayload(EventPayload):
    procurement_request_id: uuid.UUID
    approval_id: uuid.UUID


class PurchaseOrderCreatedPayload(EventPayload):
    purchase_order_id: uuid.UUID
    procurement_request_id: uuid.UUID
    po_number: str


class StockReceivedPayload(EventPayload):
    inventory_item_id: uuid.UUID
    purchase_order_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0)]


class InventoryChangedPayload(EventPayload):
    inventory_item_id: uuid.UUID
    quantity_delta: int
    quantity_on_hand: Annotated[int, Field(ge=0)]
    reserved_quantity: Annotated[int, Field(ge=0)]


class ListingDraftCreatedPayload(EventPayload):
    listing_draft_id: uuid.UUID
    inventory_item_id: uuid.UUID
    marketplace: str
    listing_version: Annotated[int, Field(gt=0)]


class ListingValidationFailedPayload(EventPayload):
    listing_draft_id: uuid.UUID
    inventory_item_id: uuid.UUID
    marketplace: str
    error_count: Annotated[int, Field(gt=0)]


class ListingApprovedPayload(EventPayload):
    listing_draft_id: uuid.UUID
    approval_id: uuid.UUID


class ListingPublishedPayload(EventPayload):
    marketplace_listing_id: uuid.UUID
    listing_draft_id: uuid.UUID
    external_listing_id: str


class OrderReceivedPayload(EventPayload):
    order_id: uuid.UUID
    marketplace: str
    external_order_id: str


class OrderCancelledPayload(EventPayload):
    order_id: uuid.UUID
    reason: str | None = None


class OrderReturnedPayload(EventPayload):
    order_id: uuid.UUID
    return_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0)]


class LowStockPayload(EventPayload):
    inventory_item_id: uuid.UUID
    sku: str
    available_quantity: Annotated[int, Field(ge=0)]
    threshold: Annotated[int, Field(ge=0)]


class CustomerMessageReceivedPayload(EventPayload):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    channel: str


class RefundRequestedPayload(EventPayload):
    refund_id: uuid.UUID
    order_id: uuid.UUID
    amount: Annotated[Decimal, Field(gt=0)]
    currency: Annotated[str, Field(min_length=3, max_length=3)]


class WorkflowFailedPayload(EventPayload):
    failed_workflow_id: uuid.UUID
    workflow_name: str
    reason: str
    retryable: bool


class EventEnvelope[PayloadT: EventPayload](BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: EventType
    event_version: Annotated[int, Field(gt=0)] = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str = "commerce-operations"
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: Annotated[int, Field(gt=0)]
    correlation_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    workflow_id: uuid.UUID | None = None
    causation_id: uuid.UUID | None = None
    idempotency_key: str
    data: PayloadT


PAYLOAD_TYPES: dict[EventType, type[EventPayload]] = {
    EventType.PRODUCT_APPROVED: ProductApprovedPayload,
    EventType.PROCUREMENT_REQUESTED: ProcurementRequestedPayload,
    EventType.PROCUREMENT_STATUS_CHANGED: ProcurementStatusChangedPayload,
    EventType.PURCHASE_APPROVED: PurchaseApprovedPayload,
    EventType.PURCHASE_ORDER_CREATED: PurchaseOrderCreatedPayload,
    EventType.STOCK_RECEIVED: StockReceivedPayload,
    EventType.INVENTORY_CHANGED: InventoryChangedPayload,
    EventType.LISTING_DRAFT_CREATED: ListingDraftCreatedPayload,
    EventType.LISTING_VALIDATION_FAILED: ListingValidationFailedPayload,
    EventType.LISTING_APPROVED: ListingApprovedPayload,
    EventType.LISTING_PUBLISHED: ListingPublishedPayload,
    EventType.ORDER_RECEIVED: OrderReceivedPayload,
    EventType.ORDER_CANCELLED: OrderCancelledPayload,
    EventType.ORDER_RETURNED: OrderReturnedPayload,
    EventType.LOW_STOCK: LowStockPayload,
    EventType.CUSTOMER_MESSAGE_RECEIVED: CustomerMessageReceivedPayload,
    EventType.REFUND_REQUESTED: RefundRequestedPayload,
    EventType.WORKFLOW_FAILED: WorkflowFailedPayload,
}

EVENT_TYPES_BY_PAYLOAD = {payload: event_type for event_type, payload in PAYLOAD_TYPES.items()}


def create_event[PayloadT: EventPayload](
    payload: PayloadT,
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    aggregate_version: int,
    idempotency_key: str,
    correlation_id: uuid.UUID | None = None,
    workflow_id: uuid.UUID | None = None,
    causation_id: uuid.UUID | None = None,
    producer: str = "commerce-operations",
) -> EventEnvelope[PayloadT]:
    try:
        event_type = EVENT_TYPES_BY_PAYLOAD[type(payload)]
    except KeyError as exc:
        raise ValueError(f"Unregistered event payload: {type(payload).__name__}") from exc
    return EventEnvelope[PayloadT](
        event_type=event_type,
        producer=producer,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        correlation_id=correlation_id or uuid.uuid4(),
        workflow_id=workflow_id,
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        data=payload,
    )


def decode_payload(event_type: EventType, data: dict[str, Any]) -> EventPayload:
    return PAYLOAD_TYPES[event_type].model_validate(data)
