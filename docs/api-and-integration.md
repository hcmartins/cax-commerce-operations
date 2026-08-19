# API and external integration

All mutation endpoints accept `Idempotency-Key`; all responses include a request/correlation ID. Commands return `202 Accepted` when workflow processing continues asynchronously. Authentication and account/role authorization are required outside local development.

## Minimum operator API

### Repository 1 intake

- `POST /api/v1/approved-products` — validate/store an approved snapshot and start procurement.

### Procurement

- `POST /api/v1/procurement-requests`
- `GET /api/v1/procurement-requests`
- `GET /api/v1/procurement-requests/{id}`
- `POST /api/v1/procurement-requests/{id}/submit-for-approval`
- `POST /api/v1/procurement-requests/{id}/order`
- `POST /api/v1/procurement-requests/{id}/mark-shipped`
- `POST /api/v1/procurement-requests/{id}/receive`
- `POST /api/v1/procurement-requests/{id}/cancel`

### Inventory

- `POST /api/v1/purchase-orders/{id}/receive`
- `GET /api/v1/inventory/{sku}`
- `POST /api/v1/inventory/items/{id}/adjustments`
- `GET /api/v1/inventory/items/{id}/movements`

### Listings and pricing

- `POST /api/v1/listing-drafts`
- `GET /api/v1/listing-drafts/{id}`
- `POST /api/v1/listing-drafts/{id}/validate`
- `POST /api/v1/listing-drafts/{id}/submit-for-approval`
- `POST /api/v1/marketplace-listings/{id}/publish`
- `POST /api/v1/pricing-decisions`
- `POST /api/v1/pricing/quotes`
- `POST /api/v1/pricing-decisions/{id}/apply`

### Orders, returns, refunds

- `POST /api/v1/orders` — ingest a versioned normalized marketplace order.
- `GET /api/v1/orders`
- `GET /api/v1/orders/{id}`
- `POST /api/v1/orders/{id}/status`
- `GET /api/v1/orders/{id}/fulfilment`
- `POST /api/v1/orders/{id}/returns`
- `POST /api/v1/orders/{id}/refunds`

### Customer service

- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{id}`
- `POST /api/v1/conversations/{id}/draft-response`
- `POST /api/v1/conversations/{id}/send-response`
- `POST /api/v1/conversations/{id}/escalate`

### Control and integrations

- `GET /api/v1/approvals`
- `POST /api/v1/approvals/{id}/decisions`
- `GET /api/v1/workflows/{id}`
- `POST /api/v1/workflows/{id}/retry`
- `POST /api/v1/webhooks/{marketplace}`
- `GET /health/live`, `GET /health/ready`

List endpoints require pagination and allow only documented filters. Webhook routes verify provider signatures before inbox persistence.

## Repository 1 contract

Repository 1 is external: no shared tables, imports, or database credentials. The initial integration is synchronous HTTP plus an equivalent event contract. The receiver returns the same resource for repeated idempotency keys.

`POST /api/v1/approved-products`

```json
{
  "schema_version": 1,
  "source_system": "commerce-intelligence",
  "source_product_id": "intel-prod-123",
  "source_workflow_run_id": "run-abc",
  "source_recommendation_id": "rec-xyz",
  "product": {
    "name": "Example product",
    "brand": null,
    "identifiers": {"gtin": null},
    "attributes": {"colour": "black"}
  },
  "selected_supplier": {
    "source_supplier_id": "supplier-42",
    "name": "Example Supplier"
  },
  "supplier_quote": {
    "source_quote_id": "quote-7",
    "currency": "GBP",
    "moq": 100,
    "quantity": 100,
    "unit_cost": "4.20",
    "shipping_cost": "80.00",
    "lead_time_days": 21,
    "valid_until": "2026-09-15"
  },
  "economics": {
    "estimated_landed_cost_per_unit": "5.00",
    "recommended_selling_price": "14.99",
    "expected_profit_per_unit": "6.50",
    "margin_percent": "43.36",
    "roi_percent": "130.00"
  },
  "recommendation": {
    "evidence": [{"type": "analysis", "reference": "evidence-9"}],
    "decided_at": "2026-08-15T12:00:00Z"
  }
}
```

Required fields are source product/run/recommendation IDs, product name, selected supplier ID/name, quote currency/MOQ/quantity/unit cost/lead time, estimated landed cost, recommended price, expected profit/margin/ROI, and at least one evidence reference. Decimal amounts are strings; dates use ISO 8601; currency uses ISO 4217. The exact example is stored at `contracts/repository-1/v1/approved-product.example.json`.

The request requires an `Idempotency-Key` header. Response: `202` with internal product, procurement request, workflow, event and correlation IDs plus a duplicate indicator. Exact retries return the original resources; changed data for an already-ingested source product returns `409`; invalid schema returns `422`.

The event alternative uses `PRODUCT_APPROVED` version 1 and the standard event envelope, with this payload under `data`. Contract fixtures live under `contracts/repository-1/v1/` and should become consumer/provider contract tests during implementation.
