import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from commerce_operations.integrations.marketplaces.connector import (
    MarketplaceConnectorError,
    MarketplaceHttpTransport,
    MarketplaceListingInput,
    MarketplaceListingResult,
    MarketplaceOrderPage,
    MarketplaceValidationResult,
)


def _required(listing: MarketplaceListingInput, *, images: bool = True) -> list[str]:
    issues = []
    for name in ("sku", "title", "description", "category"):
        if not getattr(listing, name):
            issues.append(f"{name} is required")
    if listing.quantity < 0:
        issues.append("quantity cannot be negative")
    if listing.price <= 0:
        issues.append("price must be positive")
    if images and not listing.image_urls:
        issues.append("at least one hosted image URL is required")
    return issues


class BaseConnector(ABC):
    marketplace: str

    def __init__(self, account_id: str, transport: MarketplaceHttpTransport) -> None:
        self.account_id = account_id
        self.transport = transport

    def validate_listing(self, listing: MarketplaceListingInput) -> MarketplaceValidationResult:
        issues = _required(listing)
        issues.extend(self._provider_requirements(listing))
        return MarketplaceValidationResult(not issues, tuple(issues))

    def _ensure_valid(self, listing: MarketplaceListingInput) -> None:
        result = self.validate_listing(listing)
        if not result.valid:
            from commerce_operations.integrations.marketplaces.connector import (
                MarketplaceValidationError,
            )

            raise MarketplaceValidationError(list(result.issues))

    @abstractmethod
    def _provider_requirements(self, listing: MarketplaceListingInput) -> list[str]: ...

    @staticmethod
    def _result(data: dict[str, Any], *keys: str, status: str = "published"):
        external_id = next((str(data[key]) for key in keys if data.get(key)), None)
        if external_id is None:
            raise MarketplaceConnectorError("Marketplace response omitted the listing identifier")
        return MarketplaceListingResult(external_id, status, data)


@dataclass(frozen=True)
class EbayConnectorConfig:
    access_token: str
    marketplace_id: str
    merchant_location_key: str
    category_id: str
    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str
    base_url: str = "https://api.ebay.com"


class EbayMarketplaceConnector(BaseConnector):
    marketplace = "ebay"

    def __init__(self, account_id: str, config: EbayConnectorConfig, transport) -> None:
        super().__init__(account_id, transport)
        self.config = config

    @property
    def headers(self):
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }

    def _provider_requirements(self, listing):
        return (
            ["eBay item condition is required"]
            if not listing.marketplace_payload.get("condition")
            else []
        )

    def create_listing(self, listing, *, idempotency_key):
        self._ensure_valid(listing)
        base = f"{self.config.base_url}/sell/inventory/v1"
        sku = quote(listing.sku, safe="")
        self.transport.request(
            "PUT",
            f"{base}/inventory_item/{sku}",
            headers=self.headers,
            json={
                "availability": {"shipToLocationAvailability": {"quantity": listing.quantity}},
                "condition": listing.marketplace_payload["condition"],
                "product": {
                    "title": listing.title,
                    "description": listing.description,
                    "aspects": {k: [str(v)] for k, v in listing.attributes.items()},
                    "imageUrls": list(listing.image_urls),
                },
            },
        )
        offers = self.transport.request(
            "GET",
            f"{base}/offer",
            headers=self.headers,
            params={"sku": listing.sku, "marketplace_id": self.config.marketplace_id},
        ).data.get("offers", [])
        if offers and offers[0].get("listing", {}).get("listingId"):
            return self._result(offers[0]["listing"], "listingId")
        if offers:
            offer_id = str(offers[0]["offerId"])
        else:
            offer = self.transport.request(
                "POST",
                f"{base}/offer",
                headers=self.headers,
                json={
                    "sku": listing.sku,
                    "marketplaceId": self.config.marketplace_id,
                    "format": "FIXED_PRICE",
                    "availableQuantity": listing.quantity,
                    "categoryId": listing.category or self.config.category_id,
                    "merchantLocationKey": self.config.merchant_location_key,
                    "listingDuration": "GTC",
                    "pricingSummary": {
                        "price": {"value": str(listing.price), "currency": listing.currency}
                    },
                    "listingPolicies": {
                        "fulfillmentPolicyId": self.config.fulfillment_policy_id,
                        "paymentPolicyId": self.config.payment_policy_id,
                        "returnPolicyId": self.config.return_policy_id,
                    },
                },
            )
            offer_id = str(offer.data["offerId"])
        published = self.transport.request(
            "POST",
            f"{base}/offer/{quote(offer_id, safe='')}/publish",
            headers=self.headers,
            json={"clientReferenceId": idempotency_key},
        )
        return self._result(published.data, "listingId")

    def update_listing(self, external_listing_id, listing):
        return self.create_listing(listing, idempotency_key=f"update:{external_listing_id}")

    def end_listing(self, external_listing_id):
        encoded_id = quote(external_listing_id, safe="")
        self.transport.request(
            "POST",
            f"{self.config.base_url}/sell/inventory/v1/offer/{encoded_id}/withdraw",
            headers=self.headers,
        )

    def get_listing(self, external_listing_id):
        data = self.transport.request(
            "GET",
            f"{self.config.base_url}/sell/inventory/v1/offer/{quote(external_listing_id, safe='')}",
            headers=self.headers,
        ).data
        return self._result(data, "listingId", "offerId", status=str(data.get("status", "unknown")))

    def get_orders(self, *, created_after, cursor=None):
        data = self.transport.request(
            "GET",
            f"{self.config.base_url}/sell/fulfillment/v1/order",
            headers=self.headers,
            params={"filter": f"creationdate:[{created_after}..]", "offset": cursor or "0"},
        ).data
        return MarketplaceOrderPage(tuple(data.get("orders", [])), data.get("next"))


@dataclass(frozen=True)
class AmazonConnectorConfig:
    access_token: str
    seller_id: str
    marketplace_id: str
    base_url: str = "https://sellingpartnerapi-eu.amazon.com"


class AmazonMarketplaceConnector(BaseConnector):
    marketplace = "amazon"

    def __init__(self, account_id: str, config: AmazonConnectorConfig, transport) -> None:
        super().__init__(account_id, transport)
        self.config = config

    @property
    def headers(self):
        return {"x-amz-access-token": self.config.access_token, "Content-Type": "application/json"}

    def _provider_requirements(self, listing):
        return (
            ["Amazon product_type is required"]
            if not listing.marketplace_payload.get("product_type")
            else []
        )

    def _url(self, sku):
        seller_id = quote(self.config.seller_id, safe="")
        encoded_sku = quote(sku, safe="")
        return f"{self.config.base_url}/listings/2021-08-01/items/{seller_id}/{encoded_sku}"

    def create_listing(self, listing, *, idempotency_key):
        self._ensure_valid(listing)
        data = self.transport.request(
            "PUT",
            self._url(listing.sku),
            headers={**self.headers, "x-amzn-idempotency-key": idempotency_key},
            params={"marketplaceIds": self.config.marketplace_id},
            json={
                "productType": listing.marketplace_payload["product_type"],
                "requirements": "LISTING",
                "attributes": listing.marketplace_payload.get(
                    "amazon_attributes", listing.attributes
                ),
            },
        ).data
        if data.get("status") == "INVALID":
            raise MarketplaceConnectorError("Amazon rejected the listing submission")
        return MarketplaceListingResult(
            listing.sku, str(data.get("status", "ACCEPTED")).lower(), data
        )

    def update_listing(self, external_listing_id, listing):
        return self.create_listing(listing, idempotency_key=f"update:{external_listing_id}")

    def end_listing(self, external_listing_id):
        self.transport.request(
            "DELETE",
            self._url(external_listing_id),
            headers=self.headers,
            params={"marketplaceIds": self.config.marketplace_id},
        )

    def get_listing(self, external_listing_id):
        data = self.transport.request(
            "GET",
            self._url(external_listing_id),
            headers=self.headers,
            params={
                "marketplaceIds": self.config.marketplace_id,
                "includedData": "summaries,issues,offers",
            },
        ).data
        return MarketplaceListingResult(external_listing_id, "active", data)

    def get_orders(self, *, created_after, cursor=None):
        params = {"marketplaceIds": self.config.marketplace_id, "createdAfter": created_after}
        if cursor:
            params = {"nextToken": cursor}
        data = self.transport.request(
            "GET",
            f"{self.config.base_url}/orders/2026-01-01/orders",
            headers=self.headers,
            params=params,
        ).data
        return MarketplaceOrderPage(
            tuple(data.get("orders", [])), data.get("pagination", {}).get("nextToken")
        )


@dataclass(frozen=True)
class FacebookConnectorConfig:
    access_token: str
    catalog_id: str
    commerce_account_id: str
    api_version: str = "v23.0"
    base_url: str = "https://graph.facebook.com"


class FacebookMarketplaceConnector(BaseConnector):
    marketplace = "facebook"

    def __init__(self, account_id: str, config: FacebookConnectorConfig, transport) -> None:
        super().__init__(account_id, transport)
        self.config = config

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.config.access_token}"}

    def _provider_requirements(self, listing):
        return (
            ["Facebook product URL is required"]
            if not listing.marketplace_payload.get("link")
            else []
        )

    def create_listing(self, listing, *, idempotency_key):
        self._ensure_valid(listing)
        data = self.transport.request(
            "POST",
            f"{self.config.base_url}/{self.config.api_version}/{self.config.catalog_id}/products",
            headers=self.headers,
            json={
                "retailer_id": listing.sku,
                "name": listing.title,
                "description": listing.description,
                "availability": "in stock" if listing.quantity else "out of stock",
                "condition": listing.marketplace_payload.get("condition", "new"),
                "price": f"{listing.price} {listing.currency}",
                "link": listing.marketplace_payload["link"],
                "image_url": listing.image_urls[0],
                "idempotency_key": idempotency_key,
            },
        ).data
        return self._result(data, "id", "retailer_id")

    def update_listing(self, external_listing_id, listing):
        encoded_id = quote(external_listing_id, safe="")
        data = self.transport.request(
            "POST",
            f"{self.config.base_url}/{self.config.api_version}/{encoded_id}",
            headers=self.headers,
            json={
                "name": listing.title,
                "description": listing.description,
                "price": f"{listing.price} {listing.currency}",
            },
        ).data
        return MarketplaceListingResult(external_listing_id, "published", data)

    def end_listing(self, external_listing_id):
        encoded_id = quote(external_listing_id, safe="")
        self.transport.request(
            "DELETE",
            f"{self.config.base_url}/{self.config.api_version}/{encoded_id}",
            headers=self.headers,
        )

    def get_listing(self, external_listing_id):
        encoded_id = quote(external_listing_id, safe="")
        data = self.transport.request(
            "GET",
            f"{self.config.base_url}/{self.config.api_version}/{encoded_id}",
            headers=self.headers,
        ).data
        return MarketplaceListingResult(external_listing_id, "active", data)

    def get_orders(self, *, created_after, cursor=None):
        params = {"updated_after": created_after}
        if cursor:
            params["after"] = cursor
        data = self.transport.request(
            "GET",
            f"{self.config.base_url}/{self.config.api_version}/{self.config.commerce_account_id}/orders",
            headers=self.headers,
            params=params,
        ).data
        return MarketplaceOrderPage(
            tuple(data.get("data", [])), data.get("paging", {}).get("cursors", {}).get("after")
        )


@dataclass(frozen=True)
class TikTokConnectorConfig:
    access_token: str
    app_key: str
    app_secret: str
    shop_cipher: str
    base_url: str = "https://open-api.tiktokglobalshop.com"


class TikTokMarketplaceConnector(BaseConnector):
    marketplace = "tiktok"

    def __init__(self, account_id: str, config: TikTokConnectorConfig, transport) -> None:
        super().__init__(account_id, transport)
        self.config = config

    def _provider_requirements(self, listing):
        return (
            ["TikTok category_id is required"]
            if not listing.marketplace_payload.get("category_id")
            else []
        )

    def _auth(self, path: str, params: dict[str, Any], body: Any = None):
        timestamp = str(int(time.time()))
        signed_params = {
            "app_key": self.config.app_key,
            "shop_cipher": self.config.shop_cipher,
            "timestamp": timestamp,
            **params,
        }
        parameter_string = "".join(f"{key}{signed_params[key]}" for key in sorted(signed_params))
        body_string = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
        message = (
            f"{self.config.app_secret}{path}{parameter_string}{body_string}{self.config.app_secret}"
        )
        sign = hmac.new(
            self.config.app_secret.encode(), message.encode(), hashlib.sha256
        ).hexdigest()
        headers = {
            "x-tts-access-token": self.config.access_token,
            "Content-Type": "application/json",
        }
        return headers, {**signed_params, "sign": sign}

    def _request(self, method, path, *, json=None, params=None):
        headers, auth = self._auth(path, params or {}, json)
        return self.transport.request(
            method, self.config.base_url + path, headers=headers, params=auth, json=json
        ).data

    def create_listing(self, listing, *, idempotency_key):
        self._ensure_valid(listing)
        data = self._request(
            "POST",
            "/product/202309/products",
            json={
                "idempotency_key": idempotency_key,
                "title": listing.title,
                "description": listing.description,
                "category_id": listing.marketplace_payload["category_id"],
                "main_images": [{"uri": url} for url in listing.image_urls],
                "skus": listing.marketplace_payload.get(
                    "tiktok_skus",
                    [
                        {
                            "seller_sku": listing.sku,
                            "price": {"amount": str(listing.price), "currency": listing.currency},
                            "inventory": [{"quantity": listing.quantity}],
                        }
                    ],
                ),
            },
        )
        return self._result(data.get("data", data), "product_id", "id")

    def update_listing(self, external_listing_id, listing):
        data = self._request(
            "PUT",
            f"/product/202309/products/{quote(external_listing_id, safe='')}",
            json=listing.marketplace_payload,
        )
        return MarketplaceListingResult(external_listing_id, "pending", data)

    def end_listing(self, external_listing_id):
        self._request(
            "POST",
            "/product/202309/products/deactivate",
            json={"product_ids": [external_listing_id]},
        )

    def get_listing(self, external_listing_id):
        data = self._request(
            "GET", f"/product/202309/products/{quote(external_listing_id, safe='')}"
        )
        return MarketplaceListingResult(
            external_listing_id, str(data.get("status", "unknown")), data
        )

    def get_orders(self, *, created_after, cursor=None):
        data = self._request(
            "POST",
            "/order/202309/orders/search",
            params={"page_token": cursor} if cursor else None,
            json={"create_time_ge": created_after},
        )
        body = data.get("data", data)
        return MarketplaceOrderPage(tuple(body.get("orders", [])), body.get("next_page_token"))
