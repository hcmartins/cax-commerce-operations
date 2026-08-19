import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class PricingDecisionRequest(BaseModel):
    inventory_item_id: uuid.UUID
    landed_cost: Decimal | None = Field(default=None, ge=0, decimal_places=4)
    marketplace_fees: Decimal = Field(ge=0, decimal_places=4)
    fulfilment_costs: Decimal = Field(ge=0, decimal_places=4)
    target_margin: Decimal | None = Field(default=None, ge=0, lt=1)
    minimum_margin: Decimal | None = Field(default=None, ge=0, lt=1)
    current_selling_price: Decimal | None = Field(default=None, gt=0, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3)
    reason: str = Field(min_length=1, max_length=2000)
    requester: str = Field(min_length=1, max_length=255)
    minimum_price: Decimal | None = Field(default=None, ge=0)
    maximum_price: Decimal | None = Field(default=None, gt=0)
    rounding_increment: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_margins(self):
        if (
            self.target_margin is not None
            and self.minimum_margin is not None
            and self.target_margin < self.minimum_margin
        ):
            raise ValueError("target_margin must be at least minimum_margin")
        return self


class PricingDecisionResponse(BaseModel):
    id: uuid.UUID
    inventory_item_id: uuid.UUID
    landed_cost: Decimal
    landed_cost_source: str
    marketplace_fees: Decimal
    fulfilment_costs: Decimal
    gross_profit: Decimal
    contribution_profit: Decimal
    margin_percentage: Decimal
    roi_percentage: Decimal | None
    minimum_selling_price: Decimal
    recommended_selling_price: Decimal
    current_selling_price: Decimal | None
    price_change_percentage: Decimal | None
    currency: str
    policy_result: str
    approval_id: uuid.UUID | None
    formula_version: int
    effective_at: datetime | None
    created_at: datetime
