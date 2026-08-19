# Implementation sequence

Deliver vertical slices with migrations, API, domain tests, audit, metrics and operational runbooks included in each phase.

## Phase 1 — Foundation

- Packaging, configuration, local Compose, CI and test conventions.
- PostgreSQL session/unit of work, Alembic and base IDs/timestamps.
- Event envelope, transactional outbox/inbox and worker dispatcher.
- Audit log, approval lifecycle/policy evaluator, workflow run/checkpoints.
- Authentication/roles and health endpoints.
- Repository 1 v1 intake contract, idempotency and contract tests.

Exit: repeated product intake produces one traceable procurement proposal and approval request.

## Phase 2 — Procurement and inventory

- Product/supplier/quote snapshots, requests and PO state machine.
- Manual/export supplier adapter first.
- Goods receipt, immutable stock ledger, atomic reservations, thresholds.
- Actual landed-cost reconciliation and core admin APIs.

Exit: an approved PO can be received once and produces correct, auditable stock.

## Phase 3 — Listings and first connector

- LLM provider abstraction with structured output, cost logging and prompt versions.
- Versioned drafts, deterministic validation, previews and approvals.
- Choose one marketplace based on actual business need; implement connector plus sandbox contract tests.

Exit: received stock produces an approved draft that publishes exactly once.

## Phase 4 — Orders

- Signed webhook inbox, normalisation and controlled polling reconciliation.
- Order state machine, atomic reservation/release/shipment, oversell tests.
- Manual fulfilment boundary and availability sync.

Exit: duplicate/reordered webhooks cannot duplicate orders or stock effects.

## Phase 5 — Customer service

- Minimal conversation/order context projection, risk rules, intent classification.
- Draft/approve/send flow; allowlisted auto-response; retention/redaction.

## Phase 6 — Pricing

- Versioned deterministic formulas, marketplace fee inputs, floors and approval bands.
- Recommendations first; carefully gated automatic small changes later.

## Phase 7 — Advanced orchestration

- Rich recovery UI, dead-letter replay, circuit breakers, workflow version migration.
- Enable evidence-backed low-risk autonomy per account.

## Phase 8 — Analytics and hardening

- Operational/profitability read models, SLOs, alerts, backup/restore drills.
- Load/security testing, cost dashboards, data retention and compliance review.

Do not build all tables and endpoints before the first slice. Implement only records needed by the active phase while preserving the documented boundaries and contracts.
