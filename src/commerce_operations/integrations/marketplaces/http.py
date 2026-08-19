from collections.abc import Mapping
from typing import Any

import httpx

from commerce_operations.integrations.marketplaces.connector import (
    HttpResponse,
    MarketplaceConnectorError,
)


class HttpxMarketplaceTransport:
    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> HttpResponse:
        try:
            response = httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketplaceConnectorError(f"Marketplace HTTP request failed: {exc}") from exc
        data = response.json() if response.content else {}
        return HttpResponse(response.status_code, data, response.headers)
