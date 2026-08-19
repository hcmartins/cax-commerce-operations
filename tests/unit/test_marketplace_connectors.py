from decimal import Decimal

import pytest

from commerce_operations.config import Settings
from commerce_operations.integrations.marketplaces import (
    AmazonConnectorConfig,
    AmazonMarketplaceConnector,
    EbayConnectorConfig,
    EbayMarketplaceConnector,
    FacebookConnectorConfig,
    FacebookMarketplaceConnector,
    MarketplaceListingInput,
    TikTokConnectorConfig,
    TikTokMarketplaceConnector,
)
from commerce_operations.integrations.marketplaces.connector import HttpResponse
from commerce_operations.integrations.marketplaces.factory import (
    MarketplaceConfigurationError,
    build_marketplace_connectors,
)


class MockTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return HttpResponse(200, self.responses.pop(0))


def listing(**payload_overrides):
    payload = {
        "condition": "NEW",
        "product_type": "BOTTLE",
        "link": "https://shop.example/products/bottle",
        "category_id": "123",
    }
    payload.update(payload_overrides)
    return MarketplaceListingInput(
        sku="BOTTLE-1",
        title="Insulated Bottle",
        description="Reusable insulated bottle",
        category="123",
        attributes={"material": "steel"},
        price=Decimal("14.99"),
        currency="GBP",
        quantity=8,
        image_urls=("https://cdn.example/bottle.jpg",),
        marketplace_payload=payload,
    )


def test_ebay_inventory_offer_publication_and_existing_listing_idempotency():
    transport = MockTransport([{}, {"offers": []}, {"offerId": "offer-1"}, {"listingId": "ebay-1"}])
    connector = EbayMarketplaceConnector(
        "ebay-account",
        EbayConnectorConfig(
            access_token="secret",
            marketplace_id="EBAY_GB",
            merchant_location_key="warehouse",
            category_id="123",
            fulfillment_policy_id="f",
            payment_policy_id="p",
            return_policy_id="r",
        ),
        transport,
    )
    result = connector.create_listing(listing(), idempotency_key="draft-1")
    assert result.external_listing_id == "ebay-1"
    assert [request[0] for request in transport.requests] == ["PUT", "GET", "POST", "POST"]

    retry_transport = MockTransport([{}, {"offers": [{"listing": {"listingId": "ebay-1"}}]}])
    retry = EbayMarketplaceConnector("ebay-account", connector.config, retry_transport)
    assert (
        retry.create_listing(listing(), idempotency_key="draft-1").external_listing_id == "ebay-1"
    )
    assert len(retry_transport.requests) == 2


def test_amazon_uses_sku_as_stable_external_identity():
    transport = MockTransport([{"status": "ACCEPTED", "submissionId": "submission-1"}])
    connector = AmazonMarketplaceConnector(
        "amazon-account",
        AmazonConnectorConfig("secret", "seller-1", "A1F83G8C2ARO7P"),
        transport,
    )
    result = connector.create_listing(listing(), idempotency_key="draft-1")
    assert result.external_listing_id == "BOTTLE-1"
    assert transport.requests[0][0] == "PUT"
    assert transport.requests[0][2]["headers"]["x-amzn-idempotency-key"] == "draft-1"


def test_facebook_uses_catalog_product_endpoint_and_retailer_sku():
    transport = MockTransport([{"id": "facebook-1"}])
    connector = FacebookMarketplaceConnector(
        "facebook-account",
        FacebookConnectorConfig("secret", "catalog-1", "commerce-1"),
        transport,
    )
    assert (
        connector.create_listing(listing(), idempotency_key="draft-1").external_listing_id
        == "facebook-1"
    )
    assert transport.requests[0][2]["json"]["retailer_id"] == "BOTTLE-1"


def test_tiktok_passes_native_create_product_idempotency_key():
    transport = MockTransport([{"data": {"product_id": "tiktok-1"}}])
    connector = TikTokMarketplaceConnector(
        "tiktok-account",
        TikTokConnectorConfig("secret", "app-key", "app-secret", "shop-cipher"),
        transport,
    )
    result = connector.create_listing(listing(), idempotency_key="draft-1")
    assert result.external_listing_id == "tiktok-1"
    assert transport.requests[0][2]["json"]["idempotency_key"] == "draft-1"
    assert "app-secret" not in str(transport.requests)


@pytest.mark.parametrize("marketplace_payload", [{}, {"condition": "NEW"}])
def test_validation_rejects_missing_provider_requirements(marketplace_payload):
    transport = MockTransport([])
    connector = AmazonMarketplaceConnector(
        "amazon-account",
        AmazonConnectorConfig("secret", "seller-1", "marketplace-1"),
        transport,
    )
    candidate = listing(**marketplace_payload)
    candidate = MarketplaceListingInput(
        **{**candidate.__dict__, "marketplace_payload": marketplace_payload}
    )
    assert connector.validate_listing(candidate).valid is False


def test_connectors_are_disabled_by_default_and_enabled_connectors_require_secrets():
    assert build_marketplace_connectors(Settings(environment="test", _env_file=None))
    settings = Settings(
        environment="test",
        enabled_marketplaces=["ebay"],
        _env_file=None,
    )
    with pytest.raises(MarketplaceConfigurationError, match="ebay connector"):
        build_marketplace_connectors(settings)
