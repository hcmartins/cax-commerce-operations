import pytest
from fastapi.testclient import TestClient

from commerce_operations.config import Settings
from commerce_operations.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="postgresql+psycopg://test:test@localhost:5432/test",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def approved_product_payload() -> dict:
    return {
        "schema_version": 1,
        "source_system": "commerce-intelligence",
        "source_product_id": "intel-product-123",
        "source_workflow_run_id": "intel-run-456",
        "source_recommendation_id": "recommendation-789",
        "product": {
            "name": "Reusable water bottle",
            "brand": None,
            "identifiers": {"gtin": None},
            "attributes": {"colour": "black", "capacity_ml": 750},
        },
        "selected_supplier": {
            "source_supplier_id": "supplier-42",
            "name": "Example Supplier",
            "contact_details": {"email": "supplier@example.test"},
            "terms": {"incoterm": "FOB"},
        },
        "supplier_quote": {
            "source_quote_id": "quote-7",
            "currency": "gbp",
            "moq": 100,
            "quantity": 100,
            "unit_cost": "4.20",
            "shipping_cost": "80.00",
            "lead_time_days": 21,
            "valid_until": "2026-09-15",
        },
        "economics": {
            "estimated_landed_cost_per_unit": "5.00",
            "recommended_selling_price": "14.99",
            "expected_profit_per_unit": "6.50",
            "margin_percent": "43.36",
            "roi_percent": "130.00",
        },
        "recommendation": {
            "evidence": [
                {
                    "type": "profitability-analysis",
                    "reference": "evidence-9",
                    "summary": "Meets target ROI",
                }
            ],
            "decided_at": "2026-08-15T09:00:00Z",
        },
    }
