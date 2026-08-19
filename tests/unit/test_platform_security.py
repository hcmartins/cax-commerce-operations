import json
import logging
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from commerce_operations.ai import LLMUsage, SpendingLimitExceeded, UsageAccounting
from commerce_operations.config import Settings
from commerce_operations.main import create_app
from commerce_operations.observability.logging import JsonFormatter
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import RunStatus
from commerce_operations.persistence.models import AgentRun, WorkflowRun


def secured_settings(**overrides):
    values = {
        "environment": "test",
        "api_auth_enabled": True,
        "api_keys": {
            "reader": SecretStr("reader-secret"),
            "operator": SecretStr("operator-secret"),
            "approver": SecretStr("approver-secret"),
            "admin": SecretStr("admin-secret"),
        },
        "api_roles": {
            "reader": ["viewer"],
            "operator": ["operator"],
            "approver": ["approver"],
            "admin": ["admin"],
        },
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_authentication_rbac_and_trace_headers():
    with TestClient(create_app(secured_settings())) as client:
        assert client.get("/api/v1/").status_code == 401
        assert client.get("/api/v1/", headers={"X-API-Key": "wrong"}).status_code == 401
        response = client.get(
            "/api/v1/",
            headers={
                "X-API-Key": "reader-secret",
                "X-Correlation-ID": "invalid",
                "X-Request-ID": "invalid",
            },
        )
        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] != "invalid"
        assert response.headers["X-Request-ID"] != "invalid"
        denied = client.post(
            "/api/v1/approved-products",
            headers={"X-API-Key": "reader-secret"},
            json={},
        )
        assert denied.status_code == 403


def test_metrics_require_admin_and_rate_limit_is_enforced():
    settings = secured_settings(rate_limit_requests=2)
    with TestClient(create_app(settings)) as client:
        assert client.get("/metrics", headers={"X-API-Key": "reader-secret"}).status_code == 403
        assert client.get("/metrics", headers={"X-API-Key": "admin-secret"}).status_code == 200
        assert client.get("/api/v1/", headers={"X-API-Key": "operator-secret"}).status_code == 200
        assert client.get("/api/v1/", headers={"X-API-Key": "operator-secret"}).status_code == 200
        limited = client.get("/api/v1/", headers={"X-API-Key": "operator-secret"})
        assert limited.status_code == 429
        assert limited.headers["Retry-After"] == "1"


def test_json_logging_redacts_credentials_and_customer_identifiers():
    record = logging.LogRecord(
        "security",
        logging.ERROR,
        __file__,
        1,
        "authorization=Bearer-token password=hunter2 customer_email=user@example.test",
        (),
        None,
    )
    output = json.loads(JsonFormatter().format(record))
    assert "Bearer-token" not in output["message"]
    assert "hunter2" not in output["message"]
    assert "user@example.test" not in output["message"]
    assert output["message"].count("[REDACTED]") == 3


def test_usage_accounting_tracks_agent_and_workflow_cost_and_enforces_limit(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'usage.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        workflow = WorkflowRun(
            workflow_name="cost-test",
            workflow_version=1,
            correlation_id=__import__("uuid").uuid4(),
            status=RunStatus.RUNNING,
            cost_amount=Decimal("0"),
        )
        session.add(workflow)
        session.flush()
        run = AgentRun(workflow_run_id=workflow.id, agent_type="test", status=RunStatus.RUNNING)
        session.add(run)
        session.flush()
        accounting = UsageAccounting(
            monthly_limit=Decimal("10"), workflow_limit=Decimal("1"), currency="USD"
        )
        accounting.record(
            workflow,
            run,
            LLMUsage(100, 25, Decimal("1.00"), "USD"),
        )
        assert run.input_tokens == 100
        assert workflow.cost_amount == Decimal("1.00")
        with pytest.raises(SpendingLimitExceeded, match="Workflow"):
            accounting.authorize(session, workflow)
    engine.dispose()


def test_secret_settings_do_not_reveal_values():
    settings = secured_settings()
    assert "reader-secret" not in str(settings)
    assert settings.api_keys["reader"].get_secret_value() == "reader-secret"
