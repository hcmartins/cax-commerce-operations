"""Marketplace adapter ports and configuration-driven listing implementation."""

from commerce_operations.integrations.marketplaces.connector import (
    MarketplaceConnector,
    MarketplaceConnectorRegistry,
    MarketplaceListingInput,
    MarketplaceListingResult,
    MarketplaceOrderPage,
    MarketplaceValidationError,
    MarketplaceValidationResult,
)
from commerce_operations.integrations.marketplaces.connectors import (
    AmazonConnectorConfig,
    AmazonMarketplaceConnector,
    EbayConnectorConfig,
    EbayMarketplaceConnector,
    FacebookConnectorConfig,
    FacebookMarketplaceConnector,
    TikTokConnectorConfig,
    TikTokMarketplaceConnector,
)
from commerce_operations.integrations.marketplaces.factory import (
    MarketplaceConfigurationError,
    build_marketplace_connectors,
)
from commerce_operations.integrations.marketplaces.http import HttpxMarketplaceTransport
from commerce_operations.integrations.marketplaces.listing import (
    ConfiguredMarketplaceListingAdapter,
    MarketplaceListingAdapter,
    MarketplaceListingAdapterRegistry,
    MarketplaceListingRequirements,
)
from commerce_operations.integrations.marketplaces.orders import (
    MarketplaceOrderNormalizer,
    NormalizedMarketplaceOrder,
    NormalizedOrderItem,
)

__all__ = [
    "ConfiguredMarketplaceListingAdapter",
    "AmazonConnectorConfig",
    "AmazonMarketplaceConnector",
    "EbayConnectorConfig",
    "EbayMarketplaceConnector",
    "FacebookConnectorConfig",
    "FacebookMarketplaceConnector",
    "HttpxMarketplaceTransport",
    "MarketplaceConfigurationError",
    "MarketplaceConnector",
    "MarketplaceConnectorRegistry",
    "MarketplaceListingAdapter",
    "MarketplaceListingAdapterRegistry",
    "MarketplaceListingRequirements",
    "MarketplaceListingInput",
    "MarketplaceListingResult",
    "MarketplaceOrderNormalizer",
    "MarketplaceOrderPage",
    "MarketplaceValidationError",
    "MarketplaceValidationResult",
    "NormalizedMarketplaceOrder",
    "NormalizedOrderItem",
    "TikTokConnectorConfig",
    "TikTokMarketplaceConnector",
    "build_marketplace_connectors",
]
