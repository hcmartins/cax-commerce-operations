from commerce_operations.config import Settings
from commerce_operations.integrations.marketplaces.connector import MarketplaceConnectorRegistry
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
from commerce_operations.integrations.marketplaces.http import HttpxMarketplaceTransport


class MarketplaceConfigurationError(ValueError):
    pass


def _required(marketplace: str, **values):
    missing = [name for name, value in values.items() if value in (None, "")]
    if missing:
        raise MarketplaceConfigurationError(
            f"{marketplace} connector is missing configuration: {', '.join(missing)}"
        )
    return values


def build_marketplace_connectors(settings: Settings) -> MarketplaceConnectorRegistry:
    registry = MarketplaceConnectorRegistry()
    if settings.demo_mode:
        # Demo records include synthetic sandbox publication results. Never construct a connector
        # carrying real credentials while the application is explicitly in Demo Mode.
        return registry
    transport = HttpxMarketplaceTransport(timeout_seconds=settings.marketplace_http_timeout_seconds)
    for marketplace in settings.enabled_marketplaces:
        if marketplace == "ebay":
            values = _required(
                marketplace,
                access_token=settings.ebay_access_token,
                account_id=settings.ebay_account_id,
                merchant_location_key=settings.ebay_merchant_location_key,
                category_id=settings.ebay_category_id,
                fulfillment_policy_id=settings.ebay_fulfillment_policy_id,
                payment_policy_id=settings.ebay_payment_policy_id,
                return_policy_id=settings.ebay_return_policy_id,
            )
            registry.register(
                EbayMarketplaceConnector(
                    values["account_id"],
                    EbayConnectorConfig(
                        access_token=values["access_token"].get_secret_value(),
                        marketplace_id=settings.ebay_marketplace_id,
                        merchant_location_key=values["merchant_location_key"],
                        category_id=values["category_id"],
                        fulfillment_policy_id=values["fulfillment_policy_id"],
                        payment_policy_id=values["payment_policy_id"],
                        return_policy_id=values["return_policy_id"],
                        base_url=settings.ebay_endpoint,
                    ),
                    transport,
                )
            )
        elif marketplace == "amazon":
            values = _required(
                marketplace,
                access_token=settings.amazon_access_token,
                seller_id=settings.amazon_seller_id,
                marketplace_id=settings.amazon_marketplace_id,
            )
            registry.register(
                AmazonMarketplaceConnector(
                    values["seller_id"],
                    AmazonConnectorConfig(
                        access_token=values["access_token"].get_secret_value(),
                        seller_id=values["seller_id"],
                        marketplace_id=values["marketplace_id"],
                        base_url=settings.amazon_endpoint,
                    ),
                    transport,
                )
            )
        elif marketplace == "facebook":
            values = _required(
                marketplace,
                access_token=settings.facebook_access_token,
                catalog_id=settings.facebook_catalog_id,
                commerce_account_id=settings.facebook_commerce_account_id,
            )
            registry.register(
                FacebookMarketplaceConnector(
                    values["commerce_account_id"],
                    FacebookConnectorConfig(
                        access_token=values["access_token"].get_secret_value(),
                        catalog_id=values["catalog_id"],
                        commerce_account_id=values["commerce_account_id"],
                        api_version=settings.facebook_api_version,
                        base_url=settings.facebook_endpoint,
                    ),
                    transport,
                )
            )
        elif marketplace == "tiktok":
            values = _required(
                marketplace,
                access_token=settings.tiktok_access_token,
                app_key=settings.tiktok_app_key,
                app_secret=settings.tiktok_app_secret,
                shop_cipher=settings.tiktok_shop_cipher,
            )
            registry.register(
                TikTokMarketplaceConnector(
                    values["shop_cipher"].get_secret_value(),
                    TikTokConnectorConfig(
                        access_token=values["access_token"].get_secret_value(),
                        app_key=values["app_key"],
                        app_secret=values["app_secret"].get_secret_value(),
                        shop_cipher=values["shop_cipher"].get_secret_value(),
                        base_url=settings.tiktok_endpoint,
                    ),
                    transport,
                )
            )
        else:
            raise MarketplaceConfigurationError(f"Unsupported marketplace: {marketplace}")
    return registry
