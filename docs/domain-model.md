# Domain boundaries and minimum data model

All entities use UUID primary keys, `created_at`, `updated_at`, and optimistic version numbers where concurrent writes matter. External IDs are scoped by provider. Sensitive payloads are minimised or encrypted, with retention policies.

## Catalog and traceability

- **Product**: external product ID, source run/workflow ID, name, brand, identifiers, attributes, evidence snapshot/reference, lifecycle state.
- **Supplier**: identity, external reference, contact metadata, terms, status.
- **SupplierQuote**: product/supplier, MOQ, quantity breaks, unit cost, shipping estimate, currency, lead time, validity, raw-source reference.

Catalog records represent the approved product snapshot received from Repository 1. They do not become a shared database between repositories.

## Procurement aggregate

- **ProcurementRequest**: product, selected quote, requested quantity, estimated landed cost, recommendation context, status (`proposed`, `awaiting_approval`, `ordered`, `shipped`, `received`, `cancelled`).
- **PurchaseOrder**: immutable PO number, supplier, approved monetary totals, currency, terms, external reference, ordered/shipped/received dates, status.
- Purchase-order line details are retained with the PO even if upstream product data later changes.

Only an approved request can create/submit a purchase order. Actual landed costs are reconciled when goods arrive and retained separately from estimates.

## Inventory aggregate

- **InventoryItem**: SKU, product, location, on-hand, reserved, available projection, weighted/selected cost basis, low-stock threshold, version.
- **InventoryMovement**: immutable ledger entry, type (`receipt`, `reservation`, `release`, `shipment`, `return`, `adjustment`), quantity delta, unit cost, reason, source type/ID, idempotency key.

`available = on_hand - reserved`. Balances and movements update in one transaction. Negative available inventory is rejected unless an explicit future back-order policy permits it.

## Listing aggregate

- **ListingDraft**: inventory item, marketplace, version, title, bullets, description, category, attributes, keywords, proposed price, image requirements, provider/prompt metadata, validation results, approval state.
- **MarketplaceListing**: marketplace/account, SKU, current draft/version, external listing ID, publication status, last error, published/synchronised timestamps.

Drafts are immutable versions. Publication always targets an approved version.

## Pricing aggregate

- **PricingDecision**: SKU/listing, actual or estimated landed-cost source, marketplace and fulfilment
  costs, minimum/target margins, deterministic profit/ROI outputs, commercial-rule snapshot,
  recommended and profitability-floor prices, policy result, approval reference and effective time.

Calculations are deterministic and preserve inputs and formula version.

## Order aggregate

- **Order**: marketplace/account, external order ID, status (`pending`, `paid`, `processing`, `dispatched`, `delivered`, `cancelled`, `returned`, `refunded`), currency, totals, minimal customer/shipping data, timestamps.
- **OrderItem**: order, internal SKU, external line ID, quantity, unit price, tax/discount, reservation reference.
- **Return**: order/items, quantities, reason, disposition, status, received time.
- **Refund**: order/return, amount, currency, reason, status, external refund ID, approval reference.

The unique key `(marketplace_account_id, external_order_id)` prevents duplicate order ingestion. Reservation uses a row lock or atomic conditional update.

## Customer service aggregate

- **CustomerConversation**: marketplace/account, customer pseudonymous reference, related order/product, classification, risk, status, assignee.
- **CustomerMessage**: conversation, direction, channel, redacted content, intent, safety decision and
  risk reasons, generated/final response, AI authorship and usage trace, approval reference, external
  message ID, response and delivery timestamps.

Access is scoped to the conversation and related records. Legal threats, fraud, disputes, unusual refunds, safety issues, and sensitive complaints force escalation.

## Control plane

- **Approval**: action type, resource, requested payload/hash, risk, rule/version, status, requester/decider, rationale, expiry and decision timestamps.
- **WorkflowRun**: workflow name/version, correlation, status, current step, attempts, next retry, error and checkpoints.
- **AgentRun**: agent/type, workflow, input/output references, model/provider, prompt version, token/cost usage, safety result, status.
- **DomainEvent**: immutable event envelope/payload, aggregate/version, correlation/causation, publication state.
- **AuditEvent**: actor, action, resource, before/after references or safe diff, reason, request/correlation, timestamp.

## Supporting records

Implementation will also need practical supporting tables: marketplace accounts, storage locations, webhook receipts/inbox, outbox messages, dead-letter records, integration credentials references (never raw secrets), and rate-limit state. These are infrastructure records rather than new business domains.
