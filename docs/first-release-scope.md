# First-release scope

## Include

The first useful release covers Repository 1 intake, procurement proposal/approval, manual supplier PO export/status, goods receipt, inventory ledger and balances, one listing marketplace, listing generation/validation/approval/publication, basic webhook order ingestion/reservation, audit, workflow visibility and essential operations telemetry.

## Deliberately exclude

- Real supplier payments, bank access, credit or automated fund commitment.
- Multiple marketplace connectors before one is reliable end to end.
- Automated supplier negotiation or supplier discovery (owned upstream).
- Full warehouse management: bins, picking routes, barcode hardware, lots/serials.
- Multi-warehouse allocation, bundles/kits, back-orders and advanced forecasting.
- Automated fulfilment/carrier purchasing until a fulfilment partner is selected.
- Fully autonomous refunds, purchasing, publication, complaints or large repricing.
- Dynamic competitor scraping and real-time repricing.
- Custom ML training, vector databases or broad retrieval infrastructure.
- A general-purpose autonomous agent or agent-to-agent framework.
- Kafka, Kubernetes, service mesh, microservices, event sourcing and CQRS as defaults.
- A bespoke workflow engine before simple persisted workflows prove insufficient.
- Full BI/data warehouse; begin with operational queries and a few read models.
- Native mobile apps or elaborate operator UI; start with API and a minimal admin surface.
- International tax, customs automation and multi-entity accounting beyond captured fields.

These are deferred, not prohibited. Each requires a measured business need, security/compliance review, and an explicit phase decision.
