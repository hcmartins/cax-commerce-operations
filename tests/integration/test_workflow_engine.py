import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.approvals.engine import ApprovalEngine
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.events import DatabaseEventStore, create_event
from commerce_operations.events.types import ProductApprovedPayload
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import RunStatus
from commerce_operations.persistence.models import Approval, AuditEvent, DomainEvent, WorkflowRun
from commerce_operations.workflows import (
    NonRetryableWorkflowError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowExecutionPolicy,
    WorkflowOutcome,
)


def setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'workflows.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        event = DatabaseEventStore().publish(
            session,
            create_event(
                ProductApprovedPayload(product_id=uuid.uuid4(), source_product_id="source-product"),
                aggregate_type="product",
                aggregate_id=uuid.uuid4(),
                aggregate_version=1,
                idempotency_key=f"workflow-test:{uuid.uuid4()}",
            ),
        )
        event_id = event.id
    return engine, factory, event_id


def envelope(session, event_id):
    return DatabaseEventStore().to_envelope(session.get(DomainEvent, event_id))


def test_workflow_is_idempotent_and_audits_state_history(tmp_path):
    engine, factory, event_id = setup(tmp_path)
    calls = []

    def step(event, workflow, session):
        calls.append(event.event_id)
        return WorkflowOutcome.completed("done", result="ok")

    runtime = WorkflowEngine()
    runtime.register(WorkflowDefinition("test-idempotency", 1, step))
    with factory.begin() as session:
        first = runtime.handle_event("test-idempotency", envelope(session, event_id), session)
        workflow_id = first.id
    with factory.begin() as session:
        duplicate = runtime.handle_event("test-idempotency", envelope(session, event_id), session)
        assert duplicate.id == workflow_id
        assert duplicate.status is RunStatus.SUCCEEDED
    assert calls == [event_id]
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.resource_id == workflow_id)
            )
            == 3
        )
    engine.dispose()


def test_transient_failure_schedules_retry_then_resumes(tmp_path):
    engine, factory, event_id = setup(tmp_path)
    calls = 0

    def step(event, workflow, session):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider outage")
        return WorkflowOutcome.completed("recovered")

    runtime = WorkflowEngine()
    runtime.register(
        WorkflowDefinition(
            "test-retry",
            1,
            step,
            WorkflowExecutionPolicy(max_attempts=3, timeout_seconds=300, retry_delay_seconds=5),
        )
    )
    started = datetime.now(UTC)
    with factory.begin() as session:
        workflow = runtime.handle_event(
            "test-retry", envelope(session, event_id), session, now=started
        )
        workflow_id = workflow.id
        assert workflow.status is RunStatus.RETRYING
        assert workflow.attempts == 1
    with factory.begin() as session:
        early = runtime.resume(session, workflow_id, now=started + timedelta(seconds=1))
        assert early.status is RunStatus.RETRYING
    with factory.begin() as session:
        recovered = runtime.resume(session, workflow_id, now=started + timedelta(seconds=6))
        assert recovered.status is RunStatus.SUCCEEDED
        assert recovered.attempts == 2
    assert calls == 2
    engine.dispose()


def test_nonretryable_missing_information_fails_safely_and_emits_event(tmp_path):
    engine, factory, event_id = setup(tmp_path)

    def step(event, workflow, session):
        raise NonRetryableWorkflowError("required supplier quote is missing")

    runtime = WorkflowEngine()
    runtime.register(WorkflowDefinition("test-safe-failure", 1, step))
    with factory.begin() as session:
        workflow = runtime.handle_event("test-safe-failure", envelope(session, event_id), session)
        workflow_id = workflow.id
        assert workflow.status is RunStatus.FAILED
        assert workflow.attempts == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "WORKFLOW_FAILED")
            )
            == 1
        )
        assert "supplier quote" in session.get(WorkflowRun, workflow_id).error["message"]
    engine.dispose()


def test_timeout_prevents_specialist_execution(tmp_path):
    engine, factory, event_id = setup(tmp_path)
    calls = []
    runtime = WorkflowEngine()
    runtime.register(
        WorkflowDefinition(
            "test-timeout",
            1,
            lambda event, workflow, session: calls.append(event.event_id),
            WorkflowExecutionPolicy(max_attempts=1, timeout_seconds=1),
        )
    )
    started = datetime.now(UTC)
    with factory.begin() as session:
        workflow = runtime.handle_event(
            "test-timeout", envelope(session, event_id), session, now=started
        )
        workflow_id = workflow.id
    # A completed workflow remains terminal; use another runtime event to exercise
    # pre-execution expiry.
    with factory.begin() as session:
        record = session.get(WorkflowRun, workflow_id)
        record.status = RunStatus.RETRYING
        record.completed_at = None
        record.deadline_at = started + timedelta(seconds=1)
    with factory.begin() as session:
        expired = runtime.resume(session, workflow_id, now=started + timedelta(seconds=2))
        assert expired.status is RunStatus.FAILED
        assert expired.error["message"] == "Workflow timed out"
    assert calls == [event_id]
    engine.dispose()


def test_human_approval_pauses_and_resume_completes(tmp_path):
    engine, factory, event_id = setup(tmp_path)
    approval_engine = ApprovalEngine(ApprovalPolicy([]))
    calls = 0

    def step(event, workflow, session):
        nonlocal calls
        calls += 1
        if calls == 1:
            approval = approval_engine.create_request(
                session,
                action_type="test_action",
                resource_type="workflow_run",
                resource_id=workflow.id,
                requested_action={"operation": "continue"},
                reason="Human decision required",
                requester="workflow-engine",
                risk_level="high",
                rule_name="test_rule",
                rule_version=1,
                workflow_run_id=workflow.id,
            )
            return WorkflowOutcome.waiting_for_approval("awaiting_approval", approval.id)
        return WorkflowOutcome.completed("resumed_after_approval")

    runtime = WorkflowEngine()
    runtime.register(WorkflowDefinition("test-approval", 1, step))
    with factory.begin() as session:
        workflow = runtime.handle_event("test-approval", envelope(session, event_id), session)
        workflow_id = workflow.id
        approval_id = workflow.waiting_approval_id
        assert workflow.status is RunStatus.PENDING
    with factory.begin() as session:
        approval_engine.approve(
            session,
            approval_id,
            approver="operator@example.com",
            reason="Approved to continue",
        )
    with factory.begin() as session:
        resumed = runtime.resume(session, workflow_id)
        assert resumed.status is RunStatus.SUCCEEDED
        assert resumed.current_step == "resumed_after_approval"
        assert session.get(Approval, approval_id) is not None
    assert calls == 2
    engine.dispose()
