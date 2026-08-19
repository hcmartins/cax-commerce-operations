import hashlib
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.approvals.policy import (
    ApprovalContext,
    ApprovalPolicy,
    PolicyDecision,
)
from commerce_operations.persistence.enums import ApprovalStatus, RunStatus
from commerce_operations.persistence.models import Approval, AuditEvent, WorkflowRun

ApprovalResumeHandler = Callable[[Approval, Session], None]


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalTransitionError(RuntimeError):
    pass


class ApprovalExpiredError(ApprovalTransitionError):
    pass


class ApprovalNotRequiredError(RuntimeError):
    pass


class ApprovalActionRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ApprovalResumeHandler] = {}

    def register(self, action_type: str, handler: ApprovalResumeHandler) -> None:
        if action_type in self._handlers:
            raise ValueError(f"Resume handler already registered for {action_type}")
        self._handlers[action_type] = handler

    def get(self, action_type: str) -> ApprovalResumeHandler | None:
        return self._handlers.get(action_type)


class ApprovalEngine:
    def __init__(
        self,
        policy: ApprovalPolicy,
        *,
        default_expiry_hours: int = 72,
        action_registry: ApprovalActionRegistry | None = None,
    ) -> None:
        self.policy = policy
        self.default_expiry_hours = default_expiry_hours
        self.action_registry = action_registry or ApprovalActionRegistry()

    def request_if_required(
        self,
        session: Session,
        context: ApprovalContext,
        **request: Any,
    ) -> Approval | None:
        result = self.policy.evaluate(context)
        if result.decision is PolicyDecision.ALLOW:
            return None
        assert result.matched_rule is not None
        return self.create_request(
            session,
            rule_name=result.matched_rule.name,
            rule_version=result.matched_rule.version,
            **request,
        )

    def create_request(
        self,
        session: Session,
        *,
        action_type: str,
        resource_type: str,
        resource_id: uuid.UUID,
        requested_action: dict[str, Any],
        reason: str,
        requester: str,
        risk_level: str,
        rule_name: str,
        rule_version: int,
        workflow_run_id: uuid.UUID | None = None,
        expires_at: datetime | None = None,
    ) -> Approval:
        action_hash = self._action_hash(action_type, resource_id, requested_action)
        existing = session.scalar(
            select(Approval).where(
                Approval.resource_type == resource_type,
                Approval.resource_id == resource_id,
                Approval.action_hash == action_hash,
            )
        )
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        approval = Approval(
            action_type=action_type,
            resource_type=resource_type,
            resource_id=resource_id,
            requested_payload=requested_action,
            action_hash=action_hash,
            risk_level=risk_level,
            rule_name=rule_name,
            rule_version=rule_version,
            status=ApprovalStatus.PENDING,
            requested_by=requester,
            requested_reason=reason,
            workflow_run_id=workflow_run_id,
            expires_at=expires_at or now + timedelta(hours=self.default_expiry_hours),
        )
        session.add(approval)
        session.flush()
        self._audit(session, approval, "approval.requested", requester, None, "pending")
        return approval

    def get(
        self, session: Session, approval_id: uuid.UUID, *, for_update: bool = False
    ) -> Approval:
        statement = select(Approval).where(Approval.id == approval_id)
        if for_update:
            statement = statement.with_for_update()
        approval = session.scalar(statement)
        if approval is None:
            raise ApprovalNotFoundError(str(approval_id))
        return approval

    def list_pending(self, session: Session, *, limit: int = 100) -> Sequence[Approval]:
        return session.scalars(
            select(Approval)
            .where(Approval.status == ApprovalStatus.PENDING)
            .order_by(Approval.created_at)
            .limit(limit)
        ).all()

    def approve(
        self,
        session: Session,
        approval_id: uuid.UUID,
        *,
        approver: str,
        reason: str,
        now: datetime | None = None,
    ) -> Approval:
        now = now or datetime.now(UTC)
        approval = self.get(session, approval_id, for_update=True)
        self._require_pending(approval)
        if approval.expires_at is not None and self._as_utc(approval.expires_at) <= now:
            self._expire(session, approval, now, "system")
            raise ApprovalExpiredError(f"Approval {approval.id} has expired")

        approval.status = ApprovalStatus.APPROVED
        approval.decided_by = approver
        approval.rationale = reason
        approval.decided_at = now
        self._resume_action(session, approval, now)
        self._audit(session, approval, "approval.approved", approver, "pending", "approved")
        session.flush()
        return approval

    def reject(
        self,
        session: Session,
        approval_id: uuid.UUID,
        *,
        approver: str,
        reason: str,
        now: datetime | None = None,
    ) -> Approval:
        now = now or datetime.now(UTC)
        approval = self.get(session, approval_id, for_update=True)
        self._require_pending(approval)
        if approval.expires_at is not None and self._as_utc(approval.expires_at) <= now:
            self._expire(session, approval, now, "system")
            raise ApprovalExpiredError(f"Approval {approval.id} has expired")
        approval.status = ApprovalStatus.REJECTED
        approval.decided_by = approver
        approval.rationale = reason
        approval.decided_at = now
        self._audit(session, approval, "approval.rejected", approver, "pending", "rejected")
        session.flush()
        return approval

    def expire_due(self, session: Session, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        approvals = session.scalars(
            select(Approval)
            .where(
                Approval.status == ApprovalStatus.PENDING,
                Approval.expires_at.is_not(None),
                Approval.expires_at <= now,
            )
            .with_for_update()
        ).all()
        for approval in approvals:
            self._expire(session, approval, now, "system")
        session.flush()
        return len(approvals)

    def _resume_action(self, session: Session, approval: Approval, now: datetime) -> None:
        handler = self.action_registry.get(approval.action_type)
        if handler is not None:
            handler(approval, session)
        if approval.workflow_run_id is not None:
            workflow = session.get(WorkflowRun, approval.workflow_run_id)
            if workflow is None:
                raise ApprovalTransitionError(
                    f"Workflow {approval.workflow_run_id} linked to approval was not found"
                )
            workflow.status = RunStatus.RUNNING
            workflow.next_retry_at = None
            workflow.error = None
        approval.resumed_at = now

    def _expire(self, session: Session, approval: Approval, now: datetime, actor: str) -> None:
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        approval.decided_by = actor
        approval.rationale = "Approval expired before a decision was made"
        self._audit(session, approval, "approval.expired", actor, "pending", "expired")

    @staticmethod
    def _require_pending(approval: Approval) -> None:
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalTransitionError(
                f"Approval {approval.id} is already {approval.status.value}"
            )

    @staticmethod
    def _action_hash(
        action_type: str, resource_id: uuid.UUID, requested_action: dict[str, Any]
    ) -> str:
        canonical = json.dumps(
            {
                "action_type": action_type,
                "resource_id": str(resource_id),
                "requested_action": requested_action,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _audit(
        session: Session,
        approval: Approval,
        action: str,
        actor: str,
        before_status: str | None,
        after_status: str,
    ) -> None:
        session.add(
            AuditEvent(
                actor_type="user" if actor != "system" else "system",
                actor_id=actor,
                action=action,
                resource_type="approval",
                resource_id=approval.id,
                before_state={"status": before_status} if before_status else None,
                after_state={"status": after_status},
                reason=approval.rationale or approval.requested_reason,
                correlation_id=approval.id,
            )
        )
