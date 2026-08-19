import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from commerce_operations.persistence.enums import InventoryMovementType


class InventoryItemResponse(BaseModel):
    id: uuid.UUID
    sku: str
    product_id: uuid.UUID
    product_name: str
    storage_location: str
    quantity_on_hand: int
    reserved_quantity: int
    available_quantity: int
    cost_basis: Decimal
    currency: str
    low_stock_threshold: int
    created_at: datetime
    updated_at: datetime


class InventoryLookupResponse(BaseModel):
    items: list[InventoryItemResponse]
    count: int


class InventoryMovementResponse(BaseModel):
    id: uuid.UUID
    inventory_item_id: uuid.UUID
    purchase_order_id: uuid.UUID | None
    movement_type: InventoryMovementType
    quantity_delta: int
    unit_cost: Decimal | None
    reason: str
    source_type: str
    source_id: str
    idempotency_key: str
    created_at: datetime


class StockHistoryResponse(BaseModel):
    items: list[InventoryMovementResponse]
    count: int


class GoodsReceiptRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    storage_location: str = Field(min_length=1, max_length=255)
    quantity_received: int = Field(gt=0)
    landed_unit_cost: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    low_stock_threshold: int = Field(default=0, ge=0)
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)
    received_at: datetime | None = None


class InventoryAdjustmentRequest(BaseModel):
    quantity_delta: int
    unit_cost: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class InventoryChangeResponse(BaseModel):
    inventory: InventoryItemResponse
    movement: InventoryMovementResponse
    duplicate: bool
