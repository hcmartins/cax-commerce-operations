import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    EventEnvelope,
    EventPayload,
    WorkflowFailedPayload,
    create_event,
)
from commerce_operations.persistence.enums import ApprovalStatus, RunStatus
from commerce_operations.persistence.models import Approval, AuditEvent, DomainEvent, WorkflowRun


class NonRetryableWorkflowError(RuntimeError):
    """A missing fact or invalid state that automation must not guess around."""


WorkflowStep = Callable[
    [EventEnvelope[EventPayload], WorkflowRun, Session], "WorkflowOutcome | None"
]


@dataclass(frozen=True)
class WorkflowExecutionPolicy:
    max_attempts: int = 3
    timeout_seconds: int = 300
    retry_delay_seconds: int = 30


@dataclass(frozen=True)
class WorkflowOutcome:
    current_step: str
    checkpoints: dict[str, Any] = field(default_factory=dict)
    approval_id: uuid.UUID | None = None

    @classmethod
    def completed(cls, current_step: str, **checkpoints: Any) -> "WorkflowOutcome":
        return cls(current_step, checkpoints)

    @classmethod
    def waiting_for_approval(
        cls, current_step: str, approval_id: uuid.UUID, **checkpoints: Any
    ) -> "WorkflowOutcome":
        return cls(current_step, checkpoints, approval_id)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: int
    step: WorkflowStep
    policy: WorkflowExecutionPolicy = WorkflowExecutionPolicy()


class WorkflowEngine:
    prefix = "orchestrator:"

    def __init__(self, event_store: DatabaseEventStore | None = None) -> None:
        self.event_store = event_store or DatabaseEventStore()
        self._definitions: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Workflow definition already registered: {definition.name}")
        if definition.policy.max_attempts < 1 or definition.policy.timeout_seconds < 1:
            raise ValueError("Workflow retry and timeout policy must be positive")
        self._definitions[definition.name] = definition

    def handle_event(
        self,
        definition_name: str,
        event: EventEnvelope[EventPayload],
        session: Session,
        *,
        now: datetime | None = None,
    ) -> WorkflowRun:
        definition = self._definition(definition_name)
        now = now or datetime.now(UTC)
        key = f"workflow:{definition.name}:{event.event_id}"
        workflow = session.scalar(
            select(WorkflowRun).where(WorkflowRun.idempotency_key == key).with_for_update()
        )
        if workflow is None:
            workflow = WorkflowRun(
                workflow_name=self.prefix + definition.name,
                workflow_version=definition.version,
                correlation_id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                source_event_id=event.event_id,
                idempotency_key=key,
                status=RunStatus.PENDING,
                current_step="queued",
                checkpoints={},
                max_attempts=definition.policy.max_attempts,
                timeout_seconds=definition.policy.timeout_seconds,
                started_at=now,
                deadline_at=now + timedelta(seconds=definition.policy.timeout_seconds),
            )
            session.add(workflow)
            session.flush()
            self._audit(session, workflow, None, RunStatus.PENDING, "Workflow created")
        if workflow.status in {RunStatus.SUCCEEDED, RunStatus.CANCELLED, RunStatus.FAILED}:
            return workflow
        return self._execute(session, workflow, definition, event, now)

    def resume(
        self, session: Session, workflow_id: uuid.UUID, *, now: datetime | None = None
    ) -> WorkflowRun:
        workflow = session.scalar(
            select(WorkflowRun).where(WorkflowRun.id == workflow_id).with_for_update()
        )
        if workflow is None:
            raise LookupError(str(workflow_id))
        definition_name = workflow.workflow_name.removeprefix(self.prefix)
        definition = self._definition(definition_name)
        if workflow.source_event_id is None:
            return self._terminal_failure(
                session, workflow, "Workflow source event is missing", retryable=False
            )
        if workflow.waiting_approval_id is not None:
            approval = session.get(Approval, workflow.waiting_approval_id)
            if approval is None or approval.status is not ApprovalStatus.APPROVED:
                raise NonRetryableWorkflowError("Workflow approval has not been granted")
            workflow.waiting_approval_id = None
        current_time = now or datetime.now(UTC)
        next_retry = self._utc(workflow.next_retry_at)
        if next_retry is not None and current_time < next_retry:
            return workflow
        event_record = session.get(DomainEvent, workflow.source_event_id)
        if event_record is None:
            return self._terminal_failure(
                session, workflow, "Workflow source event was not found", retryable=False
            )
        return self._execute(
            session,
            workflow,
            definition,
            self.event_store.to_envelope(event_record),
            current_time,
        )

    def retry_due(
        self, session: Session, *, now: datetime | None = None, limit: int = 100
    ) -> list[WorkflowRun]:
        """Resume retryable runs whose backoff has elapsed (scheduler entry point)."""
        current_time = now or datetime.now(UTC)
        due = session.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.workflow_name.startswith(self.prefix),
                WorkflowRun.status == RunStatus.RETRYING,
                WorkflowRun.next_retry_at <= current_time,
            )
            .order_by(WorkflowRun.next_retry_at, WorkflowRun.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return [self.resume(session, workflow.id, now=current_time) for workflow in due]

    def _execute(self, session, workflow, definition, event, now):
        deadline = self._utc(workflow.deadline_at)
        if deadline is not None and now >= deadline:
            return self._terminal_failure(session, workflow, "Workflow timed out", retryable=False)
        previous = workflow.status
        workflow.status = RunStatus.RUNNING
        workflow.current_step = "executing"
        workflow.attempts += 1
        workflow.next_retry_at = None
        session.flush()
        self._audit(session, workflow, previous, RunStatus.RUNNING, "Workflow attempt started")
        try:
            with session.begin_nested():
                outcome = definition.step(event, workflow, session)
        except NonRetryableWorkflowError as exc:
            return self._terminal_failure(session, workflow, str(exc), retryable=False)
        except Exception as exc:
            if workflow.attempts >= workflow.max_attempts:
                return self._terminal_failure(
                    session,
                    workflow,
                    f"{type(exc).__name__}: {exc}",
                    retryable=False,
                )
            previous = workflow.status
            workflow.status = RunStatus.RETRYING
            workflow.current_step = "retry_scheduled"
            workflow.next_retry_at = now + timedelta(
                seconds=definition.policy.retry_delay_seconds * workflow.attempts
            )
            workflow.error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "retryable": True,
            }
            self._audit(session, workflow, previous, RunStatus.RETRYING, str(exc))
            return workflow

        outcome = outcome or WorkflowOutcome.completed("completed")
        workflow.checkpoints = {**workflow.checkpoints, **outcome.checkpoints}
        workflow.current_step = outcome.current_step
        workflow.error = None
        if outcome.approval_id is not None:
            previous = workflow.status
            workflow.status = RunStatus.PENDING
            workflow.waiting_approval_id = outcome.approval_id
            self._audit(
                session, workflow, previous, RunStatus.PENDING, "Waiting for human approval"
            )
        else:
            previous = workflow.status
            workflow.status = RunStatus.SUCCEEDED
            workflow.completed_at = now
            self._audit(session, workflow, previous, RunStatus.SUCCEEDED, "Workflow completed")
        session.flush()
        return workflow

    def _terminal_failure(self, session, workflow, reason, *, retryable):
        previous = workflow.status
        workflow.status = RunStatus.FAILED
        workflow.current_step = "failed"
        workflow.completed_at = datetime.now(UTC)
        workflow.error = {"type": "workflow_failure", "message": reason, "retryable": retryable}
        workflow.next_retry_at = None
        self._audit(session, workflow, previous, RunStatus.FAILED, reason)
        self.event_store.publish(
            session,
            create_event(
                WorkflowFailedPayload(
                    failed_workflow_id=workflow.id,
                    workflow_name=workflow.workflow_name,
                    reason=reason,
                    retryable=retryable,
                ),
                aggregate_type="workflow_run",
                aggregate_id=workflow.id,
                aggregate_version=workflow.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"workflow-failed:{workflow.id}",
            ),
        )
        session.flush()
        return workflow

    def _definition(self, name: str) -> WorkflowDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise LookupError(f"Workflow definition is not registered: {name}") from exc

    @staticmethod
    def _utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _audit(session, workflow, before, after, reason):
        session.add(
            AuditEvent(
                actor_type="system",
                actor_id="workflow-engine",
                action="workflow.state_changed",
                resource_type="workflow_run",
                resource_id=workflow.id,
                before_state={"status": before.value} if before else None,
                after_state={
                    "status": after.value,
                    "step": workflow.current_step,
                    "attempts": workflow.attempts,
                },
                reason=reason,
                correlation_id=workflow.correlation_id,
            )
        )
