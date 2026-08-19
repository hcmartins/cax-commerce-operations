from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


class MarketplaceConnectorError(RuntimeError):
    """Base error raised at the marketplace integration boundary."""


class MarketplaceValidationError(MarketplaceConnectorError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("Marketplace validation failed: " + "; ".join(issues))
        self.issues = issues


@dataclass(frozen=True)
class MarketplaceListingInput:
    sku: str
    title: str
    description: str
    category: str | None
    attributes: dict[str, Any]
    price: Decimal
    currency: str
    quantity: int
    image_urls: tuple[str, ...]
    marketplace_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketplaceValidationResult:
    valid: bool
    issues: tuple[str, ...] = ()
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketplaceListingResult:
    external_listing_id: str
    status: str
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketplaceOrderPage:
    orders: tuple[dict[str, Any], ...]
    next_cursor: str | None = None


class MarketplaceConnector(Protocol):
    marketplace: str
    account_id: str

    def validate_listing(self, listing: MarketplaceListingInput) -> MarketplaceValidationResult: ...

    def create_listing(
        self, listing: MarketplaceListingInput, *, idempotency_key: str
    ) -> MarketplaceListingResult: ...

    def update_listing(
        self, external_listing_id: str, listing: MarketplaceListingInput
    ) -> MarketplaceListingResult: ...

    def end_listing(self, external_listing_id: str) -> None: ...

    def get_listing(self, external_listing_id: str) -> MarketplaceListingResult: ...

    def get_orders(
        self, *, created_after: str, cursor: str | None = None
    ) -> MarketplaceOrderPage: ...


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    data: dict[str, Any]
    headers: Mapping[str, str] = field(default_factory=dict)


class MarketplaceHttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> HttpResponse: ...


class MarketplaceConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, MarketplaceConnector] = {}

    def register(self, connector: MarketplaceConnector) -> None:
        if connector.marketplace in self._connectors:
            raise ValueError(f"Marketplace connector already registered: {connector.marketplace}")
        self._connectors[connector.marketplace] = connector

    def get(self, marketplace: str) -> MarketplaceConnector:
        try:
            return self._connectors[marketplace]
        except KeyError as exc:
            raise LookupError(f"Marketplace connector is not registered: {marketplace}") from exc
