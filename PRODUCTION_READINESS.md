# Production readiness review

Reviewed: 15 August 2026

## Readiness decision

The repository is ready for controlled client testing with mocked AI and marketplace sandboxes. No
known P0 issue remains after this review. It is not yet approved for unattended live marketplace
operation or public customer traffic because the P1 integration and operational controls below have
not been completed or verified.

## Architecture summary

The application is a Python modular monolith. FastAPI provides the authenticated HTTP boundary;
SQLAlchemy and PostgreSQL own transactional state; Alembic owns schema changes. A database outbox,
idempotent event receipts, and persisted workflow runs coordinate specialist application services.
Deterministic domain code owns procurement, inventory, orders, pricing and financial boundaries. AI
is restricted to structured listing/customer-service proposals behind a provider-neutral interface.
Human approval is a persisted policy-controlled boundary. Streamlit provides a separate read-only
operations dashboard.

The deployment has distinct migration, API, worker and frontend processes built from one multi-stage
Dockerfile. Redis is not currently required: PostgreSQL is the queue and source of truth.

## Implemented and verified capabilities

- Versioned approved-product intake with source traceability and idempotency.
- Procurement state machine, supplier quotes, human purchase approval and purchase orders.
- Transactional goods receipt, inventory ledger, reservations, dispatch and negative-stock guards.
- Structured marketplace listing drafts with deterministic validation, AI usage records and first-
  publication approval.
- Marketplace adapter boundary for eBay, Amazon, Facebook and TikTok, exercised with mocks only.
- Deterministic pricing floors, profit/margin/ROI calculations and price-change approvals.
- Normalized marketplace orders, duplicate delivery protection and overselling prevention.
- Customer-message risk screening, minimum-context AI classification and escalation/approval paths.
- Refund approval/resumption and return inventory/financial reconciliation.
- Durable event/workflow IDs, bounded workflow retry, timeout, approval resume and audit history.
- API-key authentication, centralized roles, production fail-closed behavior, rate limiting, trace
  IDs, JSON log redaction, metrics and an error-reporter hook.
- Read-only operations dashboard and complete product history timeline.
- Repeatable Docker startup with one migration owner, health checks and a persistent PostgreSQL
  volume.

The end-to-end suite verifies the primary journey from approved product through delivered order,
plus customer enquiry, return, refund approval, low stock, duplicates, external outages, AI failure,
workflow recovery and transaction rollback.

## P0 findings fixed in this review

1. Delayed `PRODUCT_APPROVED` and `PURCHASE_APPROVED` delivery incorrectly required the resource to
   remain in its initial state. A healthy request that had already advanced to ordered, shipped or
   received was marked failed. Orchestration now accepts valid monotonic successor states while still
   rejecting cancelled, missing or inconsistent resources. A delayed-worker end-to-end regression
   test covers the full advanced state.
2. The API approval composition omitted the refund resume handler. An approval could be recorded
   without advancing its refund. Refund approvals now use the same validated resume handler as the
   application service, and the end-to-end journey approves the refund through the real API route.

## Safe P1 fixes completed

- Authenticated approval decisions now derive the audit actor from the API principal instead of
  trusting a caller-supplied approver string. Unauthenticated local tests retain explicit actors.
- Automatically approved below-threshold refunds now write an audit event.
- The worker query filters supported event types in SQL, preventing unsupported AI events from
  starving later core events.
- Stale README statements and nonexistent directory descriptions were corrected.
- ORM metadata and the full Alembic history were compared successfully with `alembic check`.

## Security considerations

- Production API authentication is mandatory. API keys and marketplace credentials use secret
  settings and must be injected by a deployment secret manager; `.env` is ignored by Git.
- Health/readiness probes are intentionally unauthenticated. Metrics require an administrator when
  authentication is active.
- Logs do not include request bodies or query strings and redact labelled credentials, bearer tokens
  and email addresses. Customer messages remain sensitive database content and are shown only in the
  access-controlled dashboard.
- Marketplace credentials must never be placed in listing payloads, audit state, domain events or
  exception messages.
- The normalized order endpoint is an authenticated internal boundary, not a public marketplace
  webhook. It must not be exposed as a webhook until provider signature verification exists.
- No real payment execution is implemented.

## Known limitations and outstanding P1 work

1. **Specialist worker composition:** the deployed worker processes core deterministic workflows.
   Listing generation, marketplace publication and customer-service events remain pending unless a
   separately configured specialist processor is supplied. Implement and verify provider composition
   before unattended automation.
2. **Live integration certification:** marketplace connectors are mock-tested but have not been
   verified against current seller sandboxes, account scopes, rate limits, webhook signatures or
   production listing policies. AI has an interface and mocks, not a configured production provider.
3. **Event failure policy:** handler failures are retry-safe, but event-level exponential backoff,
   maximum attempts and dead-letter operations are not implemented. A persistent outage can cause
   frequent worker retries.
4. **Approval housekeeping:** expired approvals are rejected safely at decision time, but no worker
   schedule currently calls `expire_due`; pending views can retain stale records.
5. **Multi-replica controls:** metrics and API rate limiting are process-local. Use an ingress/shared
   limiter and metrics scraper before horizontally scaling.
6. **Database concurrency evidence:** transaction and contention tests use SQLite in CI. PostgreSQL
   was verified for clean migration/startup, but order/inventory race tests should also run against
   PostgreSQL in CI before sustained concurrent traffic.
7. **Audit attribution:** approval decisions bind to the authenticated principal. Other mutation
   request bodies still contain actor/requester fields and should be consistently bound to principals.
8. **Dashboard access:** the dashboard uses one shared secret and direct read-only application logic,
   not per-user SSO/RBAC. Keep it on a private network for client testing.
9. **Operational integrations:** the error-reporting hook has no configured backend. Backup/restore
   commands are documented but not scheduled, encrypted or automatically restore-tested.
10. **Financial reporting:** realised dashboard profit uses current inventory cost basis and recorded
    refunds; it is an operational estimate, not an accounting ledger with immutable sale-time COGS
    and marketplace-fee recognition.
11. **Return/refund boundary:** return reconciliation and refund approval services exist, but a
    complete authenticated marketplace return/refund ingestion API is not implemented.

## P2 post-MVP improvements

- Replace static API keys/shared dashboard access with managed OIDC, short-lived credentials and
  explicit rotation/revocation procedures.
- Export OpenTelemetry traces and durable metric histograms to the chosen monitoring platform.
- Add tamper-evident or externally archived audit records and retention policies.
- Add PostgreSQL backup automation, point-in-time recovery and regular restore drills.
- Add dependency/SBOM/container vulnerability scanning and image signing in CI.
- Move the outbox to an external broker only if measured throughput or isolation requirements justify
  the additional system; Redis/Kafka are not currently necessary.
- Resolve the upstream Starlette TestClient/httpx deprecation warning during a planned dependency
  upgrade.

## Test and verification results

The final result recorded below must be updated whenever the release candidate changes:

- `uv run ruff format --check src tests streamlit_app.py` — passed.
- `uv run ruff check .` — passed.
- `uv run pytest -q` — **139 passed**, with one non-failing upstream TestClient deprecation warning.
- Clean Alembic upgrade through `c6fa3e09b842` — passed on SQLite and PostgreSQL.
- `uv run alembic check` against a clean migrated schema — passed with no operations detected.
- Docker API, worker, frontend, migration and PostgreSQL clean startup — passed.
- API liveness/readiness, worker heartbeat and frontend health checks — passed.
- Runtime container identity — verified non-root (`commerce`).

## Deployment instructions

For local startup:

```powershell
Copy-Item .env.example .env
uv sync --dev --frozen
docker compose up --build --wait
```

For production, build immutable Docker targets, inject required settings from the deployment secret
store, run exactly one migration job, and only then roll out API, worker and frontend. Use
`compose.production.yaml` as the single-host reference, not as a substitute for the target platform's
secret, ingress, backup and monitoring controls. Full build, deployment, backup, restore and rollback
commands are in `docs/deployment.md`.

## Recommended next development priorities

1. Compose and sandbox-test the specialist AI/publication/customer-service worker paths.
2. Add event retry backoff, maximum attempts, dead-letter visibility and approval expiry scheduling.
3. Add PostgreSQL-backed concurrency and end-to-end CI jobs.
4. Implement marketplace webhook verification and a controlled return/refund intake boundary.
5. Bind all mutation audit actors to authenticated principals and add production SSO.
6. Connect metrics/error reporting and automate backup restore drills before live traffic.
