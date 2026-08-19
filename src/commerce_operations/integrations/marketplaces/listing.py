from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Protocol

from commerce_operations.agents.listing_models import GeneratedListing, ValidationIssue


@dataclass(frozen=True)
class MarketplaceListingRequirements:
    marketplace: str
    title_max_length: int
    bullet_min_count: int
    bullet_max_count: int
    bullet_max_length: int
    description_max_length: int
    keyword_max_count: int
    keyword_max_length: int
    required_attributes: tuple[str, ...] = ()
    minimum_required_images: int = 1
    minimum_price: Decimal = Decimal("0.01")
    generation_guidance: tuple[str, ...] = ()


class MarketplaceListingAdapter(Protocol):
    marketplace: str

    def generation_constraints(self) -> dict[str, Any]: ...

    def validate(
        self, listing: GeneratedListing, *, expected_sku: str
    ) -> list[ValidationIssue]: ...

    def build_payload(self, listing: GeneratedListing) -> dict[str, Any]: ...


class ConfiguredMarketplaceListingAdapter:
    """A configuration-driven adapter; no marketplace rules live in the agent prompt."""

    def __init__(self, requirements: MarketplaceListingRequirements) -> None:
        self.requirements = requirements
        self.marketplace = requirements.marketplace

    def generation_constraints(self) -> dict[str, Any]:
        constraints = asdict(self.requirements)
        constraints["minimum_price"] = str(self.requirements.minimum_price)
        return constraints

    def validate(self, listing: GeneratedListing, *, expected_sku: str) -> list[ValidationIssue]:
        requirements = self.requirements
        issues: list[ValidationIssue] = []
        self._max_length(issues, "title", listing.title, requirements.title_max_length)
        if (
            not requirements.bullet_min_count
            <= len(listing.bullet_points)
            <= requirements.bullet_max_count
        ):
            issues.append(
                ValidationIssue(
                    code="bullet_count",
                    field="bullet_points",
                    message=(
                        f"Requires {requirements.bullet_min_count}-"
                        f"{requirements.bullet_max_count} bullet points"
                    ),
                )
            )
        for index, bullet in enumerate(listing.bullet_points):
            self._max_length(
                issues,
                f"bullet_points.{index}",
                bullet,
                requirements.bullet_max_length,
            )
        self._max_length(
            issues,
            "description",
            listing.description,
            requirements.description_max_length,
        )
        if len(listing.search_terms) > requirements.keyword_max_count:
            issues.append(
                ValidationIssue(
                    code="keyword_count",
                    field="search_terms",
                    message=f"Allows at most {requirements.keyword_max_count} search terms",
                )
            )
        for index, keyword in enumerate(listing.search_terms):
            self._max_length(
                issues,
                f"search_terms.{index}",
                keyword,
                requirements.keyword_max_length,
            )
        if listing.sku != expected_sku:
            issues.append(
                ValidationIssue(
                    code="sku_mismatch",
                    field="sku",
                    message="Generated SKU does not match inventory SKU",
                )
            )
        if listing.proposed_price < requirements.minimum_price:
            issues.append(
                ValidationIssue(
                    code="price_below_marketplace_minimum",
                    field="proposed_price",
                    message=f"Price must be at least {requirements.minimum_price}",
                )
            )
        for attribute in requirements.required_attributes:
            if listing.product_attributes.get(attribute) in (None, ""):
                issues.append(
                    ValidationIssue(
                        code="required_attribute",
                        field=f"product_attributes.{attribute}",
                        message=f"Required attribute is missing: {attribute}",
                    )
                )
        required_images = sum(image.required for image in listing.image_requirements)
        if required_images < requirements.minimum_required_images:
            issues.append(
                ValidationIssue(
                    code="image_count",
                    field="image_requirements",
                    message=f"Requires at least {requirements.minimum_required_images} images",
                )
            )
        return issues

    def build_payload(self, listing: GeneratedListing) -> dict[str, Any]:
        return {
            "title": listing.title,
            "bullets": listing.bullet_points,
            "description": listing.description,
            "search_terms": listing.search_terms,
            "category": listing.category_suggestion,
            "attributes": listing.product_attributes,
            "sku": listing.sku,
            "price": str(listing.proposed_price),
            "image_requirements": [
                requirement.model_dump(mode="json") for requirement in listing.image_requirements
            ],
        }

    @staticmethod
    def _max_length(issues: list[ValidationIssue], field: str, value: str, maximum: int) -> None:
        if len(value) > maximum:
            issues.append(
                ValidationIssue(
                    code="max_length",
                    field=field,
                    message=f"Must contain at most {maximum} characters",
                )
            )


class MarketplaceListingAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, MarketplaceListingAdapter] = {}

    def register(self, adapter: MarketplaceListingAdapter) -> None:
        if adapter.marketplace in self._adapters:
            raise ValueError(f"Marketplace adapter already registered: {adapter.marketplace}")
        self._adapters[adapter.marketplace] = adapter

    def get(self, marketplace: str) -> MarketplaceListingAdapter:
        try:
            return self._adapters[marketplace]
        except KeyError as exc:
            raise LookupError(f"Marketplace adapter is not registered: {marketplace}") from exc

    def all(self) -> tuple[MarketplaceListingAdapter, ...]:
        return tuple(self._adapters.values())
