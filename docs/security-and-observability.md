# Security and observability

The API security boundary uses constant-time API-key verification and centrally enforced roles.
Keys are `SecretStr` settings intended for environment injection by the deployment secret manager.
Authentication failures do not echo credentials. Rate limiting is keyed by authenticated identity,
or client address when authentication is disabled. Multi-instance deployments should replace the
in-process limiter with a shared implementation at the gateway while retaining this local guard.

Request middleware creates request and correlation UUIDs and propagates an optional workflow UUID.
Structured logs contain stable JSON fields and timing metadata without bodies or query strings. A
redaction filter removes labelled credentials, bearer tokens, and email addresses from application
and exception messages. Domain mutations continue to write immutable `AuditEvent` records.

`/metrics` exposes request counts and cumulative request duration in Prometheus text format. The
error reporter is a provider-neutral hook, allowing Sentry or another service to be attached without
coupling the application package to its SDK.

Every AI-backed specialist records provider, model, prompt version, tokens, cost and currency on an
`AgentRun`. Listing-agent cost is aggregated into its parent workflow. `UsageAccounting` rejects a
provider call once the configured monthly or workflow ceiling has been reached. Costs in different
currencies are never silently combined.
