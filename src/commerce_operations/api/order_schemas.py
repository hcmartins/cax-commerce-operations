import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from commerce_operations.persistence.enums import OrderStatus, RunStatus


class OrderItemResponse(BaseModel):
    id: uuid.UUID
    external_line_id: str
    inventory_item_id: uuid.UUID
    sku: str
    quantity: int
    unit_price: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    reservation_reference: str | None


class OrderResponse(BaseModel):
    id: uuid.UUID
    marketplace: str
    marketplace_account_id: str
    external_order_id: str
    status: OrderStatus
    currency: str
    total_amount: Decimal
    customer_reference: str | None
    ordered_at: datetime
    paid_at: datetime | None
    dispatched_at: datetime | None
    delivered_at: datetime | None
    workflow_run_id: uuid.UUID | None
    items: list[OrderItemResponse]
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    count: int


class OrderIngestionResponse(BaseModel):
    order: OrderResponse
    duplicate: bool


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class FulfilmentStateResponse(BaseModel):
    order_id: uuid.UUID
    order_status: OrderStatus
    workflow_run_id: uuid.UUID | None
    workflow_status: RunStatus | None
    current_step: str | None
    reserved_items: int
    dispatched_at: datetime | None
    delivered_at: datetime | None
