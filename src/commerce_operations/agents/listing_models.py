from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ImageRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_type: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=1000)
    required: bool = True


class GeneratedListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    bullet_points: list[str]
    description: str = Field(min_length=1)
    search_terms: list[str]
    category_suggestion: str = Field(min_length=1)
    product_attributes: dict[str, Any]
    sku: str = Field(min_length=1)
    proposed_price: Decimal = Field(gt=0, decimal_places=4)
    image_requirements: list[ImageRequirement]


class ValidationIssue(BaseModel):
    code: str
    field: str
    message: str
