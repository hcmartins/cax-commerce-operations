# Architecture

## Shape

The first production shape is a modular monolith with two processes built from one codebase:

1. **API process** accepts commands, queries, partner webhooks, and approvals.
2. **Worker process** consumes durable events and advances workflows.

Both use PostgreSQL. Redis may broker background jobs, but durable workflow/event state remains in PostgreSQL. A scheduler performs controlled polling, retries, and outbox publication.

```text
Repository 1 / marketplaces / operator
                  |
             FastAPI boundary
                  |
       application command handlers
                  |
  domain modules + approval policy engine
                  |
   PostgreSQL transaction + event outbox
                  |
             worker dispatcher
                  |
 workflows -> specialist services -> adapters
```

## Dependency rules

- `domains/*` contains entities, value objects, state machines, domain services, errors, and repository protocols.
- `application` coordinates use cases and transactions but does not contain marketplace-specific rules.
- `agents` contains narrow listing and customer-service AI coordinators; each produces structured proposals and calls owning-domain use cases rather than owning business state.
- `api` translates HTTP to commands/queries and never manipulates ORM objects directly.
- `integrations` implements ports defined by domain/application layers.
- `persistence` implements repositories and unit-of-work boundaries.
- `orchestration` reacts to events and invokes application use cases; it does not bypass domain rules.
- `ai` returns structured proposals. It never commits money, changes stock, publishes, refunds, or sends high-risk messages directly.

Cross-domain reads use explicit application queries. Cross-domain changes use commands/events, not direct table mutation.

## Major modules

| Module | Responsibility | Explicitly does not own |
|---|---|---|
| Procurement | Quotes, requests, purchase orders, landed-cost reconciliation | Payments, stock ledger |
| Inventory | SKU, locations, balances, reservations, movements | Listing content, order lifecycle |
| Listings | Draft/version/validation/publication lifecycle | Marketplace SDK details, pricing rules |
| Pricing | Deterministic floors, margins, recommendations | Listing publication |
| Orders | Normalisation, status, lines, returns/refunds coordination | Marketplace payload parsing, stock ledger |
| Customer service | Conversation context, classification, drafts, escalation | Unbounded customer data access, direct high-risk refunds |
| Analytics | Read-only operational and profitability projections | Transactional business state |
| Approvals | Policy evaluation, decisions, expiry, evidence | Business action execution |
| Orchestration | Workflow state, step execution, retry/stop/escalate | Domain invariants |
| Events | Envelopes, outbox/inbox, dispatch, dead letters | Business workflow decisions |
| Integrations | External contract translation and rate-limit handling | Core business rules |

## Deployment evolution

Start with API, worker, PostgreSQL and optionally Redis. Add object storage for product images when listing work begins. Add a managed error/telemetry service only when useful. Split a domain into a service only after independent scaling, security isolation, team ownership, or availability needs are demonstrated.

Kafka and Kubernetes are not part of the initial design.

## Repository rules

- Each domain eventually contains `model.py`, `service.py`, `ports.py`, and domain-specific tests only when implementation starts.
- ORM mappings remain outside domain models where practical.
- All external calls carry a stable idempotency key.
- UTC timestamps and immutable audit records are mandatory.
- Currency values use decimal minor-unit-safe types plus ISO 4217 currency codes.
