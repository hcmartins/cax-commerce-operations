import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from commerce_operations.persistence.enums import ProcurementStatus, PurchaseOrderStatus


class SupplierReview(BaseModel):
    id: uuid.UUID
    name: str
    source_supplier_id: str


class QuoteReview(BaseModel):
    id: uuid.UUID
    source_quote_id: str
    moq: int
    quantity: int
    unit_cost: Decimal
    shipping_cost: Decimal
    currency: str
    lead_time_days: int
    valid_until: date | None = None


class PurchaseOrderReview(BaseModel):
    id: uuid.UUID
    po_number: str
    status: PurchaseOrderStatus
    quantity: int
    total_amount: Decimal
    currency: str
    external_reference: str | None
    ordered_at: datetime | None
    shipped_at: datetime | None
    received_at: datetime | None
    expected_arrival_at: datetime | None


class ProcurementReview(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    status: ProcurementStatus
    requested_quantity: int
    estimated_landed_cost_per_unit: Decimal
    expected_landed_cost_total: Decimal
    currency: str
    expected_arrival_at: datetime | None
    supplier: SupplierReview
    quote: QuoteReview
    purchase_order: PurchaseOrderReview | None
    workflow_run_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ProcurementListResponse(BaseModel):
    items: list[ProcurementReview]
    count: int


class ApprovalRequestCommand(BaseModel):
    requester: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalCreatedResponse(BaseModel):
    procurement: ProcurementReview
    approval_id: uuid.UUID
    approval_status: str


class StatusCommand(BaseModel):
    actor: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class MarkOrderedCommand(StatusCommand):
    external_reference: str | None = Field(default=None, max_length=255)


class MarkShippedCommand(StatusCommand):
    expected_arrival_at: datetime | None = None
