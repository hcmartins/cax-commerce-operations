"""Production smoke test for a running Commerce Operations deployment.

Exercises the deployment from the outside over real HTTP, the way an operator
or a deployment pipeline would: liveness/readiness probes, the published API
contract surface, and one representative end-to-end commerce workflow
(an approved product flowing through to a proposed procurement request).

This does not import the application; it only talks to `--base-url`, so it
is safe to run against `docker compose up` on localhost or against a staging
deployment.

Usage:
    uv run python scripts/smoke_test.py --base-url http://localhost:8000
    uv run python scripts/smoke_test.py --base-url https://staging.example.com --api-key "$KEY"
"""

from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass

import httpx


@dataclass
class SmokeStep:
    name: str
    passed: bool
    detail: str = ""


def run(base_url: str, api_key: str | None, timeout: float) -> list[SmokeStep]:
    headers = {"X-API-Key": api_key} if api_key else {}
    steps: list[SmokeStep] = []

    with httpx.Client(base_url=base_url, timeout=timeout, headers=headers) as client:
        steps.append(_check_health(client))
        steps.append(_check_ready(client))
        steps.append(_check_api_version(client))
        steps.append(_check_openapi_document(client))
        steps.extend(_check_approved_product_workflow(client))

    return steps


def _check_health(client: httpx.Client) -> SmokeStep:
    try:
        response = client.get("/health")
        ok = response.status_code == 200 and response.json().get("status") == "ok"
        return SmokeStep("GET /health", ok, f"status={response.status_code}")
    except httpx.HTTPError as exc:
        return SmokeStep("GET /health", False, str(exc))


def _check_ready(client: httpx.Client) -> SmokeStep:
    try:
        response = client.get("/ready")
        ok = response.status_code == 200 and response.json().get("status") == "ready"
        return SmokeStep("GET /ready", ok, f"status={response.status_code} body={response.text}")
    except httpx.HTTPError as exc:
        return SmokeStep("GET /ready", False, str(exc))


def _check_api_version(client: httpx.Client) -> SmokeStep:
    try:
        response = client.get("/api/v1/")
        ok = response.status_code == 200 and "version" in response.json()
        return SmokeStep("GET /api/v1/", ok, f"status={response.status_code}")
    except httpx.HTTPError as exc:
        return SmokeStep("GET /api/v1/", False, str(exc))


def _check_openapi_document(client: httpx.Client) -> SmokeStep:
    try:
        response = client.get("/openapi.json")
        if response.status_code == 404:
            # Interactive docs/OpenAPI are intentionally disabled in production.
            return SmokeStep("GET /openapi.json", True, "disabled (production)")
        ok = response.status_code == 200 and "/api/v1/approved-products" in response.json().get(
            "paths", {}
        )
        return SmokeStep("GET /openapi.json", ok, f"status={response.status_code}")
    except httpx.HTTPError as exc:
        return SmokeStep("GET /openapi.json", False, str(exc))


def _approved_product_payload(marker: str) -> dict:
    return {
        "schema_version": 1,
        "source_system": "commerce-intelligence",
        "source_product_id": f"smoke-test-{marker}",
        "source_workflow_run_id": f"smoke-run-{marker}",
        "source_recommendation_id": f"smoke-recommendation-{marker}",
        "product": {
            "name": "Smoke test product",
            "brand": None,
            "identifiers": {"gtin": None},
            "attributes": {"colour": "black"},
        },
        "selected_supplier": {
            "source_supplier_id": f"smoke-supplier-{marker}",
            "name": "Smoke Test Supplier",
            "contact_details": {},
            "terms": {"incoterm": "FOB"},
        },
        "supplier_quote": {
            "source_quote_id": f"smoke-quote-{marker}",
            "currency": "GBP",
            "moq": 10,
            "quantity": 10,
            "unit_cost": "4.20",
            "shipping_cost": "10.00",
            "lead_time_days": 14,
        },
        "economics": {
            "estimated_landed_cost_per_unit": "5.00",
            "recommended_selling_price": "14.99",
            "expected_profit_per_unit": "6.50",
            "margin_percent": "43.36",
            "roi_percent": "130.00",
        },
        "recommendation": {
            "evidence": [{"type": "smoke-test", "reference": f"smoke-{marker}"}],
            "decided_at": "2026-08-15T09:00:00Z",
        },
    }


def _check_approved_product_workflow(client: httpx.Client) -> list[SmokeStep]:
    marker = uuid.uuid4().hex[:12]
    payload = _approved_product_payload(marker)
    steps: list[SmokeStep] = []

    try:
        ingest = client.post(
            "/api/v1/approved-products",
            json=payload,
            headers={"Idempotency-Key": f"smoke-test:{marker}"},
        )
    except httpx.HTTPError as exc:
        return [SmokeStep("POST /api/v1/approved-products", False, str(exc))]

    ok = ingest.status_code == 202
    steps.append(
        SmokeStep(
            "POST /api/v1/approved-products", ok, f"status={ingest.status_code} body={ingest.text}"
        )
    )
    if not ok:
        return steps

    procurement_id = ingest.json()["procurement_request_id"]
    try:
        review = client.get(f"/api/v1/procurement-requests/{procurement_id}")
        ok = review.status_code == 200 and review.json().get("status") in (
            "proposed",
            "awaiting_approval",
            "approved",
            "ordered",
            "shipped",
            "received",
        )
        steps.append(
            SmokeStep(
                "GET /api/v1/procurement-requests/{id}",
                ok,
                f"status={review.status_code} procurement_status={review.json().get('status')}",
            )
        )
    except httpx.HTTPError as exc:
        steps.append(SmokeStep("GET /api/v1/procurement-requests/{id}", False, str(exc)))

    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=None, help="X-API-Key, if API auth is enabled")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    steps = run(args.base_url, args.api_key, args.timeout)

    failures = 0
    for step in steps:
        marker = "PASS" if step.passed else "FAIL"
        print(f"[{marker}] {step.name} {step.detail}".rstrip())
        if not step.passed:
            failures += 1

    if failures:
        print(f"\n{failures} of {len(steps)} smoke checks failed against {args.base_url}")
        return 1
    print(f"\nAll {len(steps)} smoke checks passed against {args.base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
