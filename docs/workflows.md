# End-to-end workflows

Each workflow persists its run and step results. Steps invoke application commands and can be retried safely.

## Product approval to procurement

1. Validate and deduplicate `PRODUCT_APPROVED` or HTTP intake.
2. Store the approved product, supplier, quote, economics, evidence references and source trace.
3. Create a proposed procurement request.
4. An operator reviews MOQ, quantity, costs, lead time, and arrival estimate, then submits the request for approval.
5. Recalculate expected totals deterministically, create an approval request, and stop at `awaiting_approval`.
6. On approval, atomically mark procurement `approved` and create one approved PO.
7. An operator explicitly records supplier submission (`ordered`), shipment, receipt, or cancellation. No payment capability is present.

Every status change writes both an immutable audit record and a `PROCUREMENT_STATUS_CHANGED`
event. The implemented state path is `proposed -> awaiting_approval -> approved -> ordered ->
shipped -> received`; requests may be cancelled before supplier submission. Cancelling an already
ordered or shipped PO is rejected until a dedicated high-risk cancellation approval workflow is
implemented.

## Goods receipt to listing

1. Operator records PO quantities, SKU, storage location and actual landed unit costs using a required idempotency key.
2. In one locked transaction, write receipt movements, update on-hand balance and weighted cost basis, reconcile the PO, and enqueue `STOCK_RECEIVED` plus `INVENTORY_CHANGED`.
3. Partial receipts leave the PO shipped; the final ordered unit moves procurement and the PO to received.
4. The listing agent consumes `STOCK_RECEIVED` through the retry-safe internal event processor.
5. A configured marketplace adapter supplies generation constraints to a provider-neutral structured-LLM port.
6. Deterministically validate required fields, SKU, price floor, images and marketplace constraints.
7. Store the versioned AI response, prompt/model metadata, validation result and token/cost usage.
8. Valid drafts request human approval for first publication; invalid drafts remain stored for diagnosis.

### Marketplace publication connectors

Marketplace publication is implemented behind a common connector port for eBay Inventory and
Fulfilment APIs, Amazon Listings Items and Orders APIs, Meta/Facebook Catalog and Commerce APIs, and
TikTok Shop Product and Order APIs. The `LISTING_APPROVED` consumer selects the connector named by
the immutable draft, validates hosted images and provider fields, publishes with the stable key
`listing-draft:{draft_id}`, stores the provider listing ID, and emits `LISTING_PUBLISHED`.

Connectors are disabled by default. Set `COMMERCE_ENABLED_MARKETPLACES` and provide the corresponding
secret-backed settings from `.env.example`. Provider access, seller onboarding, marketplace policies,
hosted product images, categories, and required marketplace-specific attributes must exist before a
live sandbox or production call can succeed.

Inventory adjustments use the same ledger and transaction boundary. Any change that would make
on-hand negative or reduce on-hand below reserved stock is rejected. Crossing from above the
configured threshold to at-or-below it emits `LOW_STOCK` once for that movement.

## Marketplace order to fulfilment

1. Verify the webhook, then map it through the `MarketplaceOrderNormalizer` boundary into the versioned
   `NormalizedMarketplaceOrder` contract; scheduled polling is a reconciliatory fallback.
2. Deduplicate by marketplace/account/external order and source event; reject changed redeliveries.
3. In one transaction, create the order and fulfilment workflow, then atomically reserve each SKU with
   a conditional database update. A competing order cannot reserve the same available units.
4. If any line is insufficient, roll back the order, workflow, every reservation and ledger entry.
5. Payment advances to processing; cancellation releases reservations through the inventory ledger.
6. Dispatch atomically reduces both on-hand and reserved stock and records shipment movements.
7. Delivery completes the fulfilment workflow; return/refund statuses remain explicit transitions.
8. Emit `ORDER_RECEIVED`, `INVENTORY_CHANGED`, `LOW_STOCK` on threshold crossing, and complete audit
   records. Duplicate webhook delivery creates no duplicate order, movement, reservation or event.

## Customer enquiry

1. Verify and idempotently store the inbound message, linking only its conversation and relevant order
   or product records.
2. Run deterministic legal, dispute, fraud, abusive, unusual-refund and existing-risk rules before AI.
3. If safe to classify, send only the message, marketplace/conversation state, public product facts and
   minimal order status/timestamps/SKUs to the provider-neutral structured-LLM port. Customer identity,
   shipping address, payment data and unrelated conversation history are excluded.
4. `AUTO_RESPOND`: persist an approved final response only for allowlisted low-risk intents.
5. `DRAFT_FOR_APPROVAL`: persist the generated draft and create a customer-response approval. Returns,
   refunds and complaints cannot be promoted to automatic response by the model.
6. `HUMAN_ESCALATION`: assign the human queue with immutable risk reasons. Legal threats, unusual
   refunds, disputes, suspected fraud, abusive/high-risk conversations, AI failures and unknown policy
   are non-downgradable escalations.
7. Store prompt/provider/model, structured output, tokens/cost, approval/final response, timestamps,
   audit and `CUSTOMER_MESSAGE_RECEIVED`. External marketplace delivery remains a separate adapter.

## Return and refund

1. Record return request and policy assessment.
2. Obtain approval where amount/risk requires it.
3. Send one idempotent refund request; persist provider response.
4. On physical receipt, inspect disposition and add sellable/damaged inventory movement.
5. Reconcile financial and inventory state; preserve complete audit.

## Price recommendation

1. Use actual inventory cost basis unless an explicit estimated landed cost is supplied.
2. Add fixed marketplace fees and fulfilment costs to obtain the deterministic unit cost base.
3. Calculate the minimum price from minimum margin and the recommended price from target margin.
4. Round both upward to the configured increment; reject a commercial maximum that conflicts with
   the profitability floor rather than weakening that floor.
5. Persist gross profit, contribution profit, margin percentage, ROI, rule snapshot and formula version.
6. Make only changes inside the configured safe percentage boundary effective immediately; otherwise
   create a price-change approval and leave the decision ineffective.
7. Approval rechecks the stored profitability floor before making the decision effective.

Margins are decimal ratios (`0.30` means 30%). Reported margin and ROI values are percentage values
(`30.0000` means 30%). Applying an effective decision to a marketplace remains a separate connector
operation.
# Autonomous workflow runtime

The modular monolith uses the database-backed event outbox and handler receipts as its
delivery boundary. `AutonomousOrchestrator` registers one handler per high-level route, while
`WorkflowEngine` persists an independent run keyed by the source event. This keeps retries
safe today and leaves the event transport replaceable by a broker later.

| Source event | Workflow result |
|---|---|
| `PRODUCT_APPROVED` | Validate the proposed request and selected quote for Procurement |
| `PURCHASE_APPROVED` | Validate the approved request and its Purchase Order |
| `STOCK_RECEIVED` | Invoke configured marketplace Listing Agents |
| `LISTING_APPROVED` | Validate and publish through the marketplace connector boundary |
| `ORDER_RECEIVED` | Verify normalized order, reservations, and fulfilment run |
| `LOW_STOCK` | Persist an idempotent reorder recommendation |
| `CUSTOMER_MESSAGE_RECEIVED` | Invoke Customer Service and pause for approval when required |
| `ORDER_RETURNED` | Reconcile sellable inventory, refund totals, and return/order state |

Each run persists its source event, deterministic idempotency key, correlation/workflow ID,
version, current step, checkpoints, attempts, retry time, deadline, approval reference, error,
and completion time. Every state change produces an audit record. Terminal failures publish
one idempotent `WORKFLOW_FAILED` event.

Transient exceptions schedule bounded retry with linear backoff. A scheduler calls
`WorkflowEngine.retry_due()`; approval callbacks call `resume()` after approval. Missing
records, invalid transitions, absent specialist configuration, ambiguous return disposition,
and inconsistent financial data are non-retryable failures. The runtime does not synthesize
missing facts or call marketplace/payment systems without their configured service boundary.
