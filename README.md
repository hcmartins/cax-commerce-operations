# Cax Commerce Operations Platform

Architecture and delivery blueprint for the post-approval half of an AI commerce operating system:

`Approved product -> Procurement -> Inventory -> Listings -> Orders -> Customer service -> Analytics`

The repository implements a modular-monolith operations platform with FastAPI, PostgreSQL,
database-backed events and workflows, human approvals, procurement, inventory, listing generation,
marketplace adapters, pricing, orders, customer-service automation, a worker, and a read-only
Streamlit operations dashboard. External AI providers and live marketplace accounts remain
deployment integrations and are not enabled by default.

## Design principles

- Business domains own their state and rules.
- PostgreSQL is the system of record; Redis is an optional queue/cache, not a source of truth.
- A transactional outbox publishes durable domain events after database commits.
- External systems are accessed only through versioned adapters.
- Deterministic code owns money, inventory, pricing floors, and state transitions.
- AI generates or classifies proposals; policies decide whether humans must approve them.
- Every command, event, workflow, agent action, and human decision is traceable and idempotent.
- Start as one deployable API plus one worker process; split services only when measurements justify it.

## Repository map

```text
src/commerce_operations/
  api/              HTTP boundary, schemas, dependencies, versioned routes
  domains/          Procurement, inventory, listings, pricing, orders, support
  application/      Cross-domain use cases and transaction orchestration
  agents/           Narrow AI-assisted listing and support coordinators
  workflows/        Durable workflows, retries, deduplication, recovery
  approvals/        Reusable policy evaluation and approval lifecycle
  events/           Event envelope, outbox/inbox, handlers, registry
  integrations/     Repository 1, marketplace, supplier and fulfilment adapters
  ai/               Provider abstraction, prompts, safety and usage accounting
  persistence/      Database session, ORM mappings, repositories, migrations
  security/         Authentication, authorization, secrets and data minimisation
  observability/    Logging, metrics, tracing and alert hooks
  dashboard/        Read-only operational and profitability queries
  config/           Typed runtime settings
tests/               Unit, integration, contract, workflow and end-to-end tests
contracts/           Versioned HTTP/event contracts and examples
docs/                Architecture and delivery decisions
infrastructure/      Local Docker entrypoint + GitHub OIDC setup script for CI/CD
.github/             CI/CD workflow (ci-cd.yml): test on every push/PR, build+push+deploy on push to main
streamlit_app.py     Operations dashboard entry point
```

Domain code should not import FastAPI, marketplace SDKs, worker frameworks, or LLM clients. Dependencies point inward: adapters -> application -> domain.

Marketplace connectors are disabled for local development. To enable sandbox calls, copy the relevant
variables from `.env.example`, set `COMMERCE_ENABLED_MARKETPLACES` (for example `["ebay"]`), and use
sandbox/test seller credentials. Automated tests always use mock transports and never call seller APIs.

## Documentation

- [Production readiness review](PRODUCTION_READINESS.md)
- [Architecture](docs/architecture.md)
- [Domain boundaries and data](docs/domain-model.md)
- [Events and reliability](docs/events-and-reliability.md)
- [API and Repository 1 integration](docs/api-and-integration.md)
- [End-to-end workflows](docs/workflows.md)
- [Approval and autonomy policy](docs/approvals-and-autonomy.md)
- [Implementation sequence](docs/implementation-plan.md)
- [First-release scope](docs/first-release-scope.md)
- [Deployment, backup, restore and rollback](docs/deployment.md)

## Runtime

- Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic
- PostgreSQL 16
- Redis plus a modest worker library only when asynchronous processing is introduced
- Docker Compose for local development
- pytest, Ruff, mypy, structured JSON logging, OpenTelemetry

No framework choice in this foundation prevents replacing the future worker with a durable workflow engine later.

## Prerequisites

Choose one development path:

- Local Python: Python 3.12+ and [uv](https://docs.astral.sh/uv/), plus Docker for PostgreSQL.
- Containers only: Docker Desktop with Docker Compose.

Copy `.env.example` to `.env` before changing any local settings. Defaults are suitable for the Compose database and intentionally contain development-only credentials.

The Compose PostgreSQL service is exposed on host port `55432` by default to avoid
collisions with locally installed PostgreSQL services. Keep `POSTGRES_PORT` and the
port in `COMMERCE_DATABASE_URL` aligned if you override it.

## Local development with Python

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
uv sync --dev
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn commerce_operations.main:app --reload
```

In another PowerShell window, start the read-only operations dashboard:

```powershell
uv run streamlit run streamlit_app.py
```

Open:

- API documentation: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health>
- Readiness: <http://localhost:8000/ready>
- Versioned API root: <http://localhost:8000/api/v1/>
- Operations dashboard: <http://localhost:8501>

Stop PostgreSQL with `docker compose stop postgres`. To remove the containers while retaining the named database volume, run `docker compose down`. Adding `--volumes` deletes local database data and should only be used deliberately.

## Client Demo Mode

Demo Mode uses clearly labelled synthetic records and disables construction of live marketplace
connectors. It cannot be seeded or reset in production. Set these values in `.env`:

```env
COMMERCE_DEMO_MODE=true
COMMERCE_DASHBOARD_CURRENCY=GBP
```

Create demo records idempotently and start the client demo:

```powershell
uv run python -m commerce_operations.demo seed
uv run streamlit run streamlit_app.py
```

Reset the scenario to its original state, or remove only synthetic demo records:

```powershell
uv run python -m commerce_operations.demo reset
uv run python -m commerce_operations.demo remove
```

These commands target records marked with the dedicated `commerce-demo-v1` source identifier.
They refuse to run unless `COMMERCE_DEMO_MODE=true`, and always refuse
`COMMERCE_ENVIRONMENT=production`. See [the client demo guide](docs/CLIENT_DEMO_GUIDE.md).

## Run everything with Docker

```powershell
Copy-Item .env.example .env
docker compose up --build --wait
```

The migration job runs to completion before the API, worker and dashboard start; all three then wait
on Docker health checks (`--wait` blocks until every service reports healthy or exits non-zero). The
API starts at <http://localhost:8000> and the dashboard at <http://localhost:8501>. The Compose
dashboard key is `local-dashboard` unless `COMMERCE_DASHBOARD_ACCESS_KEY` is set in `.env`. Stop them
with `Ctrl+C`, followed by `docker compose down` if required.

If port `8000`, `8501` or `55432` is already in use on your machine, override the host side before
starting Compose, for example:

```powershell
$env:API_PORT = "18000"
$env:FRONTEND_PORT = "18501"
$env:POSTGRES_PORT = "55433"
docker compose up --build --wait
```

### Production smoke test

Once the stack is healthy, verify liveness, readiness, the published OpenAPI contract and one
representative commerce workflow (an approved product flowing through to a proposed procurement
request) against the running deployment:

```powershell
uv run python scripts/smoke_test.py --base-url http://localhost:8000
```

Pass `--api-key` if `COMMERCE_API_AUTH_ENABLED=true`. The script exits non-zero if any check fails,
so it is safe to use as a post-deploy gate.

## CI/CD

[`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml): on every push/PR it runs `ruff` +
`pytest` (unit, integration, contract, workflow and e2e — all self-contained, no live database
needed); on push to `main`, it additionally builds the `api` and `migrate` images, tags both
`<app-version>-<short-sha>`, pushes them to ACR, and deploys — to DEV automatically, to PROD only
after a required reviewer approves (a GitHub Environment protection rule on `production`, not
something the workflow file itself can create — that's a manual step in this repo's own Settings).

Unlike commerce-intelligence (which migrates on container startup), this app keeps migration as a
distinct one-shot step — matching its own architecture and `docs/deployment.md` — so `deploy-dev`
and `deploy-prod` each open a temporary, uniquely-named Postgres firewall rule for the runner's
public IP, run the pushed `migrate` image against Key Vault's `commerce-operations-database-url`
secret, then remove the rule before verifying `/health` and `/ready`.

**One-time setup**, once this repo exists on GitHub:

```bash
GITHUB_OWNER=<org-or-user> GITHUB_REPO=<repo-name> \
RESOURCE_GROUP=rg-commerce-dev ACR_NAME=acrcommercedevzqbs3z \
KEY_VAULT_NAME=kv-commerce-dev-zqbs3z POSTGRES_SERVER_NAME=psql-commerce-dev-zqbs3z \
CONTAINER_APP_NAME=commerce-operations-api \
./infrastructure/setup-github-oidc.sh
```

This creates an Azure AD app registration trusted via OIDC federated credentials (no client secret
stored anywhere) — two per subject (name-based and GitHub's newer immutable owner/repo-ID format,
auto-issued for repos created/renamed/transferred on or after 2026-07-15; trusting only the old
format fails with `AADSTS700213` against such a repo). Since `rg-commerce-dev` is shared with
`commerce-intelligence-api`, the grant is scoped as narrowly as Azure allows: `Reader` on the
resource group, `AcrPush` on the registry (push+pull; ACR has no per-repository scoping to narrow
further), `Container Apps Contributor` scoped to just the `commerce-operations-api` Container App,
`Key Vault Secrets User` scoped to the vault (needed to read the database URL for migrations — Azure
RBAC has no finer-grained, per-secret role), `Contributor` scoped to just the Postgres server
resource (needed to manage the temporary migration firewall rule), and `Managed Identity Operator`
scoped to just this app's own identity resource (`az containerapp update` resubmits the Container
App's identity reference, which ARM's linked-authorization check validates against this action —
`Container Apps Contributor` alone does not include it). This identity can never touch
`commerce-intelligence-api` or the shared storage account. The script prints the exact GitHub repo
secrets/variables to set from its output. Then, in the repo's Settings:

- **Environments** → create `dev` (no protection rules) and `production` (add required reviewers —
  this is the manual-approval gate).
- Leave `PROD_AZURE_RESOURCE_GROUP` / `PROD_KEY_VAULT_NAME` / `PROD_POSTGRES_SERVER_NAME` unset until
  PROD infrastructure actually exists; `deploy-prod` skips cleanly without them rather than failing.

## Tests and quality checks

```powershell
uv sync --dev
uv run pytest
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/contract -q
uv run pytest tests/workflows -q
uv run pytest tests/e2e -q
uv run pytest --cov=commerce_operations --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
```

The current test suite is self-contained and does not require a running database. The readiness route uses a real `SELECT 1` at runtime; its success and failure paths are isolated in unit tests. Future persistence integration tests should use the Compose PostgreSQL instance.

- `tests/unit` — domain logic and API units in isolation.
- `tests/integration` — real FastAPI app plus a SQLite-backed session, covering multi-component flows (ingestion, procurement, persistence, the worker, the workflow engine).
- `tests/contract` — the published HTTP contract itself: the versioned example payload, OpenAPI document shape and API-version prefix, and strict rejection of undocumented fields.
- `tests/workflows` — the same event-driven pipeline the production worker runs (`commerce_operations.worker.CORE_EVENT_TYPES`), asserting the orchestrator-owned `WorkflowRun` reaches completion and stays idempotent across worker restarts.
- `tests/e2e` — the full approved-product-to-delivered-order journey plus recovery, duplicate and failure scenarios.
- `scripts/smoke_test.py` — a production smoke test that talks to a running deployment over real HTTP (see [Production smoke test](#production-smoke-test)).

## Database migrations

The database URL is read from `COMMERCE_DATABASE_URL`. Apply existing migrations with:

```powershell
uv run alembic upgrade head
```

After adding SQLAlchemy mappings in a later phase, import their metadata from the Alembic environment and create a reviewed migration:

```powershell
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Never rely on automatic table creation in application startup; schema changes go through reviewed migrations.

The initial persistence schema contains the catalog, procurement, inventory, listing,
pricing, order, customer-service, approval, workflow, agent-run, event-outbox, and audit
records described in the architecture documents. SQLAlchemy mappings live in
`src/commerce_operations/persistence/models.py`; they are intentionally separate from
FastAPI/Pydantic schemas. Basic data access uses `SQLAlchemyRepository`, while
`session_scope` supplies an explicit commit-or-rollback transaction boundary.

## Configuration

Settings use the `COMMERCE_` prefix and are documented in `.env.example`. Important values are:

| Variable | Purpose |
|---|---|
| `COMMERCE_ENVIRONMENT` | `local`, `test`, `staging`, or `production` |
| `COMMERCE_DEBUG` | FastAPI debug mode; keep false outside local diagnosis |
| `COMMERCE_LOG_LEVEL` | JSON application log level |
| `COMMERCE_DATABASE_URL` | SQLAlchemy PostgreSQL connection URL |
| `COMMERCE_DATABASE_POOL_SIZE` | Persistent database connections per process |
| `COMMERCE_DATABASE_MAX_OVERFLOW` | Temporary connections allowed above the pool |
| `COMMERCE_DATABASE_CONNECT_TIMEOUT_SECONDS` | Readiness/database connection timeout |
| `COMMERCE_CORS_ORIGINS` | JSON list of explicitly allowed browser origins |
| `COMMERCE_API_AUTH_ENABLED` | Require an `X-API-Key` on non-probe endpoints |
| `COMMERCE_API_KEYS` | JSON identity-to-secret map; inject from a secrets manager |
| `COMMERCE_API_ROLES` | JSON identity-to-role-list map (`viewer`, `operator`, `approver`, `admin`) |
| `COMMERCE_RATE_LIMIT_REQUESTS` | Per-principal requests allowed in each rate window |
| `COMMERCE_RATE_LIMIT_WINDOW_SECONDS` | In-process rate-limit window |
| `COMMERCE_METRICS_ENABLED` | Enable the Prometheus-compatible `/metrics` endpoint |
| `COMMERCE_AI_MONTHLY_SPENDING_LIMIT` | Optional monthly AI cost ceiling |
| `COMMERCE_WORKFLOW_SPENDING_LIMIT` | Optional cost ceiling for one workflow |
| `COMMERCE_SPENDING_CURRENCY` | Currency used to enforce configured limits |
| `COMMERCE_DASHBOARD_ACCESS_KEY` | Secret required by the production dashboard |
| `COMMERCE_DASHBOARD_CURRENCY` | Currency used for dashboard financial KPIs |
| `COMMERCE_SIGNIFICANT_PRICE_CHANGE_PERCENT` | Price-change percentage requiring approval |
| `COMMERCE_REFUND_APPROVAL_THRESHOLD` | Refund amount above which approval is required |
| `COMMERCE_DEFAULT_APPROVAL_EXPIRY_HOURS` | Default lifetime of a pending approval |
| `COMMERCE_WORKFLOW_MAX_ATTEMPTS` | Maximum attempts for an orchestration workflow |
| `COMMERCE_WORKFLOW_TIMEOUT_SECONDS` | End-to-end workflow timeout |
| `COMMERCE_WORKFLOW_RETRY_DELAY_SECONDS` | Base retry backoff; multiplied by attempt number |
| `COMMERCE_REORDER_TARGET_MULTIPLIER` | Low-stock target as a multiple of its threshold |

Interactive API documentation is disabled when `COMMERCE_ENVIRONMENT=production`.

Authentication is mandatory in production even if the enable flag is omitted. API keys are
represented as Pydantic secrets and must be supplied by the deployment secret
store, never committed to `.env`. `/health` and `/ready` remain unauthenticated for container
probes. `/metrics` requires the `admin` role when authentication is enabled. Read operations
accept viewer/operator/approver roles, mutations require operator, and approval decisions require
approver. Admin has all permissions.

Responses include generated `X-Request-ID` and `X-Correlation-ID` headers; a valid incoming UUID
is propagated. `X-Workflow-ID` is propagated when supplied as a UUID. JSON logs include these IDs
but never request bodies, query strings, API keys, authorization values, common secrets, or email
addresses. Attach an error reporter with `create_app(..., error_reporter=reporter)`; the hook receives
only exception and trace metadata.

AI providers report token and cost metadata into an `AgentRun`. Listing workflows additionally
aggregate that cost on `WorkflowRun`. Construct `UsageAccounting.from_settings(settings)` and inject
it into AI agents to enforce monthly and per-workflow limits before provider calls.

## Autonomous orchestration

`AutonomousOrchestrator` connects persisted domain events to small specialist services through
the in-process handler registry. `WorkflowEngine` owns execution state, retry scheduling,
timeouts, idempotency, approval pauses/resumption, checkpoints, failure events, and audit
records. A worker may call `WorkflowEngine.retry_due()` to run workflows whose retry backoff
has elapsed. Business rules stay in the existing procurement, inventory, listing,
publication, order, and customer-service services; the orchestrator only validates facts and
coordinates handoffs. See `docs/workflows.md` for the supported routes and failure semantics.
# cax-commerce-operations
