# Deployment and recovery

## Runtime topology

The same multi-stage `Dockerfile` produces four non-root images:

- `api` — FastAPI/Uvicorn on port 8000.
- `worker` — database-outbox event processing and due workflow retries.
- `frontend` — the read-only Streamlit operations dashboard on port 8501.
- `migrate` — the one-shot Alembic migration job.

PostgreSQL is the system of record and uses a named volume in Compose. Redis is not required: the
transactional database outbox and persisted workflow retry schedule provide the current queueing
semantics. Do not add Redis until a measured workload requires a shared cache or another queue.

## Local development from a clean checkout

```powershell
Copy-Item .env.example .env
uv sync --dev --frozen
docker compose up --build --wait
docker compose ps
```

Open the API at <http://localhost:8000/docs> and dashboard at <http://localhost:8501>. The default
local dashboard key is `local-dashboard`. Verify probes with:

```powershell
Invoke-WebRequest http://localhost:8000/health
Invoke-WebRequest http://localhost:8000/ready
Invoke-WebRequest http://localhost:8501/_stcore/health
docker compose exec worker python -m commerce_operations.worker --healthcheck
```

Stop services without deleting PostgreSQL data:

```powershell
docker compose down
```

`docker compose down --volumes` permanently removes the local database and should only be used when
deliberately resetting development data.

## Database migrations

The `migrate` service is the only process that owns schema migration. API, worker, and frontend wait
for it to complete successfully, preventing concurrent Alembic upgrades.

```powershell
docker compose run --rm migrate
docker compose run --rm migrate alembic current
```

For local Python development:

```powershell
uv run alembic upgrade head
uv run alembic current
```

Review generated revisions before committing them. Never create tables during application startup.

## Testing

```powershell
uv sync --dev --frozen
uv run ruff format --check src tests streamlit_app.py
uv run ruff check .
uv run pytest -q
uv run pytest tests/e2e -q
```

## Production build

Build immutable targets without injecting runtime secrets into image layers:

```powershell
docker build --pull --target api -t registry.example/commerce-operations-api:$env:RELEASE_TAG .
docker build --target worker -t registry.example/commerce-operations-worker:$env:RELEASE_TAG .
docker build --target frontend -t registry.example/commerce-operations-frontend:$env:RELEASE_TAG .
docker build --target migrate -t registry.example/commerce-operations-migrate:$env:RELEASE_TAG .
```

Push those exact tags and deploy by digest where the container platform supports it. Do not bake
`.env`, API keys, marketplace credentials, database passwords, or dashboard keys into an image.

## Production deployment

`compose.production.yaml` is a reference single-host deployment. Supply all required variables from
the host secret manager or CI/CD protected environment. At minimum it requires:

- `POSTGRES_PASSWORD`
- `COMMERCE_DATABASE_URL`
- `COMMERCE_API_KEYS`
- `COMMERCE_API_ROLES`
- `COMMERCE_DASHBOARD_ACCESS_KEY`

Use JSON for API identities and roles, for example values shaped like
`{"operations":"long-random-secret"}` and `{"operations":["admin"]}`. This is a shape example,
not a usable credential.

```powershell
docker compose -f compose.production.yaml config --quiet
docker compose -f compose.production.yaml up -d --build --wait
docker compose -f compose.production.yaml ps
docker compose -f compose.production.yaml logs migrate
```

For an orchestrator-managed PostgreSQL service, point `COMMERCE_DATABASE_URL` at that service and
remove the reference PostgreSQL service/volume from the deployment overlay. Run exactly one migrate
job before rolling out API, worker, and frontend replicas. Terminate TLS at the ingress/load balancer,
restrict `/metrics`, and do not expose PostgreSQL publicly.

## PostgreSQL backup

Create the destination first, then produce a PostgreSQL custom-format backup and copy it out of the
container:

```powershell
New-Item -ItemType Directory -Force backups
docker compose exec postgres pg_dump -U commerce -d commerce_operations -Fc -f /tmp/commerce.dump
docker compose cp postgres:/tmp/commerce.dump ./backups/commerce.dump
docker compose exec postgres rm -f /tmp/commerce.dump
```

Record the release tag and Alembic revision alongside each backup. Production backups should be
encrypted, access-controlled, stored outside the application host, and restore-tested regularly.

## Restore

Restore is destructive. Confirm the target database and backup before running it. Stop writers,
copy the backup into PostgreSQL, recreate the target database, restore, then run the migration job:

```powershell
docker compose stop api worker frontend
docker compose cp ./backups/commerce.dump postgres:/tmp/commerce.dump
docker compose exec postgres dropdb -U commerce --if-exists commerce_operations
docker compose exec postgres createdb -U commerce commerce_operations
docker compose exec postgres pg_restore -U commerce -d commerce_operations --exit-on-error /tmp/commerce.dump
docker compose run --rm migrate
docker compose up -d api worker frontend
```

Validate `/ready`, worker health, dashboard health, record counts, and the current Alembic revision
after restoration.

## Application rollback

1. Stop or pause new writes at the ingress and worker.
2. Inspect the failed release logs and current Alembic revision.
3. If its schema remains backward compatible, redeploy the previous immutable API, worker, frontend,
   and migration image tags without changing the database.
4. If the migration is not backward compatible, restore the pre-deployment backup. Prefer restore
   over an untested downgrade.
5. Only use `alembic downgrade <revision>` when that exact downgrade was rehearsed against a copy of
   production data.
6. Re-enable traffic after API readiness, worker heartbeat, frontend health, and a smoke journey pass.

Never use `docker compose down --volumes` as a rollback mechanism.
