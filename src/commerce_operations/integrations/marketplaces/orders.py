from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from commerce_operations.persistence.enums import OrderStatus


class NormalizedOrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    external_line_id: str = Field(min_length=1, max_length=255)
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0, decimal_places=4)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=4)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=4)


class NormalizedMarketplaceOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: int = Field(default=1, ge=1)
    marketplace: str = Field(min_length=1, max_length=100)
    marketplace_account_id: str = Field(min_length=1, max_length=255)
    external_order_id: str = Field(min_length=1, max_length=255)
    source_event_id: str = Field(min_length=1, max_length=255)
    status: OrderStatus = OrderStatus.PENDING
    currency: str = Field(min_length=3, max_length=3)
    total_amount: Decimal = Field(ge=0, decimal_places=4)
    customer_reference: str | None = Field(default=None, max_length=255)
    shipping_details: dict = Field(default_factory=dict)
    ordered_at: datetime
    items: tuple[NormalizedOrderItem, ...] = Field(min_length=1)


class MarketplaceOrderNormalizer(Protocol):
    marketplace: str

    def normalize_order(self, payload: dict) -> NormalizedMarketplaceOrder: ...
