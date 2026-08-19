# Events and reliability

## Event envelope

Every event uses a versioned envelope:

```json
{
  "event_id": "uuid",
  "event_type": "STOCK_RECEIVED",
  "event_version": 1,
  "occurred_at": "2026-08-15T12:00:00Z",
  "producer": "commerce-operations",
  "aggregate_type": "inventory_item",
  "aggregate_id": "uuid",
  "aggregate_version": 3,
  "correlation_id": "uuid",
  "causation_id": "uuid-or-null",
  "idempotency_key": "stable-business-key",
  "data": {}
}
```

Consumers key deduplication by consumer name plus event ID. Payload changes require a new event version and an upcaster or parallel handler.

## Major domain events

- `PRODUCT_APPROVED`: accepted contract from Repository 1.
- `PROCUREMENT_REQUESTED`, `PROCUREMENT_STATUS_CHANGED`, `PROCUREMENT_APPROVAL_REQUIRED`, `PURCHASE_APPROVED`.
- `PURCHASE_ORDER_CREATED`, `PURCHASE_ORDER_SUBMITTED`, `PURCHASE_SHIPPED`, `GOODS_RECEIPT_RECORDED`.
- `STOCK_RECEIVED`, `INVENTORY_ADJUSTED`, `INVENTORY_RESERVED`, `INVENTORY_RELEASED`, `LOW_STOCK`.
- `LISTING_GENERATION_REQUESTED`, `LISTING_DRAFTED`, `LISTING_VALIDATION_FAILED`, `LISTING_APPROVAL_REQUIRED`, `LISTING_APPROVED`, `LISTING_PUBLISHED`, `LISTING_PUBLICATION_FAILED`.
- `PRICE_CHANGE_PROPOSED`, `PRICE_CHANGE_APPROVAL_REQUIRED`, `PRICE_CHANGED`, `PRICE_FLOOR_BLOCKED`.
- `ORDER_RECEIVED`, `ORDER_NORMALISED`, `ORDER_REJECTED`, `ORDER_PAID`, `ORDER_DISPATCHED`, `ORDER_CANCELLED`.
- `RETURN_REQUESTED`, `ORDER_RETURNED`, `REFUND_PROPOSED`, `REFUND_APPROVED`, `REFUND_COMPLETED`, `REFUND_FAILED`.
- `CUSTOMER_MESSAGE_RECEIVED`, `CUSTOMER_RESPONSE_DRAFTED`, `CUSTOMER_RESPONSE_SENT`, `CUSTOMER_CASE_ESCALATED`.
- `APPROVAL_REQUESTED`, `APPROVAL_GRANTED`, `APPROVAL_REJECTED`, `APPROVAL_EXPIRED`.
- `WORKFLOW_FAILED`, `WORKFLOW_ESCALATED`.

## Delivery pattern

Domain mutation and outbox insert occur in the same PostgreSQL transaction. A worker publishes outbox records and marks them published. Delivery is at least once, so handlers must be idempotent. Incoming webhooks are first written to an inbox with a provider event ID/hash and acknowledged quickly.

The initial modular-monolith implementation lives in `commerce_operations.events`. It provides
typed Pydantic payloads/envelopes, a database event-store adapter, a handler registry, and a
transactional processor. `EventPublisher` is the broker boundary: a later Kafka or managed-queue
adapter can implement that port without changing event producers or handlers.

Each `(event_id, handler_name)` completion is persisted in `event_handler_receipts`. The processor
locks the event, creates the receipt, and runs the handler within one transaction. A handler failure
rolls back both its database effects and receipt, records the error/attempt on the event, and permits
a safe retry. Successful handlers are skipped on redelivery. Handlers calling external systems must
also pass the event's stable idempotency key because a database transaction cannot atomically commit
an external API call.

## Duplicate prevention

- Unique business keys for external orders, marketplace listings, refunds, receipts, and POs.
- Idempotency keys on command endpoints and outbound adapter calls.
- Inbox deduplication before handling; handler receipt stored atomically with effects.
- Aggregate versions for optimistic concurrency; row-level locking for inventory reservation.
- Publication records link a specific approved draft version to one external operation.
- Refund amount and external refund ID constraints prevent repeat financial actions.

## Retry and failure policy

- Retry only classified transient failures with exponential backoff and jitter.
- Respect provider `Retry-After` and centrally enforced account rate limits.
- Do not blindly retry validation, permission, policy, or insufficient-stock failures.
- After configured attempts, move work to a durable dead-letter state, alert, and expose replay to an authorised operator.
- Workflow checkpoints permit restart from the last completed idempotent step.
- Circuit breakers pause adapters during marketplace outages; polling can reconcile after recovery.

## Operations baseline

Structured logs include request, correlation, workflow, actor, tenant/account and event IDs without secrets or unnecessary PII. Metrics cover queue age, workflow duration/failure, retry/dead-letter counts, webhook lag, stock conflicts, adapter errors/rate limits, AI tokens/cost, and approval age. Distributed traces cross API, worker and adapters. Secrets live in environment-backed secret storage and are rotated; they never enter events or logs.
