import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Currency = Annotated[str, Field(min_length=3, max_length=3)]
Money = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]


class ProductInformationV1(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=500)]
    brand: Annotated[str, Field(max_length=255)] | None = None
    identifiers: dict[str, str | None] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)


class SelectedSupplierV1(ContractModel):
    source_supplier_id: Annotated[str, Field(min_length=1, max_length=255)]
    name: Annotated[str, Field(min_length=1, max_length=500)]
    contact_details: dict[str, Any] = Field(default_factory=dict)
    terms: dict[str, Any] = Field(default_factory=dict)


class SupplierQuoteV1(ContractModel):
    source_quote_id: Annotated[str, Field(min_length=1, max_length=255)]
    currency: Currency
    moq: Annotated[int, Field(gt=0)]
    quantity: Annotated[int, Field(gt=0)]
    unit_cost: Money
    shipping_cost: Money = Decimal("0")
    lead_time_days: Annotated[int, Field(ge=0)]
    valid_until: date | None = None
    source_reference: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value.upper()

    @model_validator(mode="after")
    def quantity_meets_moq(self) -> "SupplierQuoteV1":
        if self.quantity < self.moq:
            raise ValueError("quantity must be greater than or equal to MOQ")
        return self


class EconomicsV1(ContractModel):
    estimated_landed_cost_per_unit: Money
    recommended_selling_price: Money
    expected_profit_per_unit: Money
    margin_percent: Annotated[Decimal, Field(max_digits=9, decimal_places=4)]
    roi_percent: Annotated[Decimal, Field(max_digits=9, decimal_places=4)]


class RecommendationEvidenceV1(ContractModel):
    type: Annotated[str, Field(min_length=1, max_length=100)]
    reference: Annotated[str, Field(min_length=1, max_length=500)]
    summary: Annotated[str, Field(max_length=2000)] | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RecommendationV1(ContractModel):
    evidence: Annotated[list[RecommendationEvidenceV1], Field(min_length=1)]
    decided_at: datetime


class ApprovedProductRequestV1(ContractModel):
    schema_version: Literal[1]
    source_system: Literal["commerce-intelligence"] = "commerce-intelligence"
    source_product_id: Annotated[str, Field(min_length=1, max_length=255)]
    source_workflow_run_id: Annotated[str, Field(min_length=1, max_length=255)]
    source_recommendation_id: Annotated[str, Field(min_length=1, max_length=255)]
    product: ProductInformationV1
    selected_supplier: SelectedSupplierV1
    supplier_quote: SupplierQuoteV1
    economics: EconomicsV1
    recommendation: RecommendationV1


class ApprovedProductResponseV1(BaseModel):
    schema_version: Literal[1] = 1
    product_id: uuid.UUID
    procurement_request_id: uuid.UUID
    workflow_run_id: uuid.UUID
    event_id: uuid.UUID
    correlation_id: uuid.UUID
    procurement_status: str
    duplicate: bool
