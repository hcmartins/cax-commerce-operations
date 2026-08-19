# Repository 1 contract v1

This directory owns fixtures and, during implementation, the machine-readable JSON Schema/OpenAPI fragments for `POST /api/v1/approved-products` and `PRODUCT_APPROVED` v1.

The executable Pydantic contract is in
`src/commerce_operations/integrations/repository_1/contracts.py`; this directory contains the
provider-facing example fixture. FastAPI publishes the machine-readable schema in the application
OpenAPI document.

Compatibility rules:

- Additive optional fields are backward compatible.
- Required-field, meaning, or type changes require v2.
- Consumers ignore unknown fields and validate the declared schema version.
- Money is a decimal string plus ISO 4217 currency.
- Producer and consumer contract tests run in CI before release.
