import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.api.approvals import get_approval_engine
from commerce_operations.approvals.engine import ApprovalEngine, ApprovalTransitionError
from commerce_operations.approvals.policy import (
    ApprovalActionType,
    ApprovalContext,
    ApprovalPolicy,
)
from commerce_operations.config import Settings
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.enums import ApprovalStatus, RunStatus
from commerce_operations.persistence.models import Approval, AuditEvent, WorkflowRun


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'approvals.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture
def engine() -> ApprovalEngine:
    settings = Settings(
        significant_price_change_percent=10,
        refund_approval_threshold=50,
        default_approval_expiry_hours=72,
        _env_file=None,
    )
    return ApprovalEngine(ApprovalPolicy.from_settings(settings))


def workflow() -> WorkflowRun:
    return WorkflowRun(
        workflow_name="purchase-order",
        workflow_version=1,
        correlation_id=uuid.uuid4(),
        status=RunStatus.PENDING,
        current_step="awaiting_approval",
    )


def request_approval(
    session: Session,
    engine: ApprovalEngine,
    *,
    workflow_run_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> Approval:
    approval = engine.request_if_required(
        session,
        ApprovalContext(ApprovalActionType.SUPPLIER_PURCHASE),
        action_type=ApprovalActionType.SUPPLIER_PURCHASE.value,
        resource_type="purchase_order",
        resource_id=uuid.uuid4(),
        requested_action={"operation": "submit", "amount": "100.00"},
        reason="Commit supplier funds",
        requester="buyer@example.com",
        risk_level="financial",
        workflow_run_id=workflow_run_id,
        expires_at=expires_at,
    )
    assert approval is not None
    return approval


def test_approval_resumes_workflow_and_writes_audit(session_factory, engine) -> None:
    with session_factory.begin() as session:
        run = workflow()
        session.add(run)
        session.flush()
        approval = request_approval(session, engine, workflow_run_id=run.id)
        approval_id = approval.id
        workflow_id = run.id

    with session_factory.begin() as session:
        approved = engine.approve(
            session,
            approval_id,
            approver="finance@example.com",
            reason="Budget confirmed",
        )
        assert approved.status is ApprovalStatus.APPROVED
        assert approved.resumed_at is not None

    with session_factory() as session:
        resumed = session.get(WorkflowRun, workflow_id)
        assert resumed is not None
        assert resumed.status is RunStatus.RUNNING
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_rejection_does_not_resume_workflow(session_factory, engine) -> None:
    with session_factory.begin() as session:
        run = workflow()
        session.add(run)
        session.flush()
        approval = request_approval(session, engine, workflow_run_id=run.id)
        approval_id = approval.id
        workflow_id = run.id

    with session_factory.begin() as session:
        rejected = engine.reject(
            session,
            approval_id,
            approver="finance@example.com",
            reason="Outside budget",
        )
        assert rejected.status is ApprovalStatus.REJECTED
        assert rejected.resumed_at is None

    with session_factory() as session:
        assert session.get(WorkflowRun, workflow_id).status is RunStatus.PENDING
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_expiry_is_persisted_and_audited(session_factory, engine) -> None:
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        approval = request_approval(
            session,
            engine,
            expires_at=now - timedelta(minutes=1),
        )
        approval_id = approval.id

    with session_factory.begin() as session:
        assert engine.expire_due(session, now=now) == 1

    with session_factory() as session:
        expired = session.get(Approval, approval_id)
        assert expired is not None
        assert expired.status is ApprovalStatus.EXPIRED
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_duplicate_request_and_decision_are_safe(session_factory, engine) -> None:
    with session_factory.begin() as session:
        approval = request_approval(session, engine)
        duplicate = engine.create_request(
            session,
            action_type=approval.action_type,
            resource_type=approval.resource_type,
            resource_id=approval.resource_id,
            requested_action=approval.requested_payload,
            reason=approval.requested_reason,
            requester=approval.requested_by,
            risk_level=approval.risk_level,
            rule_name=approval.rule_name,
            rule_version=approval.rule_version,
        )
        assert duplicate.id == approval.id
        approval_id = approval.id

    with session_factory.begin() as session:
        engine.approve(
            session,
            approval_id,
            approver="finance@example.com",
            reason="Approved once",
        )

    with pytest.raises(ApprovalTransitionError), session_factory.begin() as session:
        engine.approve(
            session,
            approval_id,
            approver="finance@example.com",
            reason="Approved twice",
        )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Approval)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_approval_api_lists_inspects_and_rejects(session_factory, engine) -> None:
    with session_factory.begin() as session:
        approval = request_approval(session, engine)
        approval_id = approval.id

    application = create_app(Settings(environment="test", _env_file=None))

    def session_dependency():
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = session_dependency
    application.dependency_overrides[get_approval_engine] = lambda: engine

    with TestClient(application) as client:
        pending = client.get("/api/v1/approvals")
        inspected = client.get(f"/api/v1/approvals/{approval_id}")
        rejected = client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            json={"approver": "finance@example.com", "reason": "Outside policy"},
        )
        duplicate = client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"approver": "finance@example.com", "reason": "Changed mind"},
        )

    assert pending.status_code == 200
    assert pending.json()["count"] == 1
    assert inspected.status_code == 200
    assert inspected.json()["status"] == "pending"
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert duplicate.status_code == 409
