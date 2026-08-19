import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalActionType, ApprovalContext
from commerce_operations.persistence.enums import RefundStatus
from commerce_operations.persistence.models import Approval, AuditEvent, Refund


class RefundApprovalError(RuntimeError):
    pass


class RefundService:
    def __init__(self, approval_engine: ApprovalEngine) -> None:
        self.approval_engine = approval_engine

    def request_approval(
        self, session: Session, refund_id: uuid.UUID, *, requester: str, reason: str
    ) -> Approval | None:
        refund = session.get(Refund, refund_id)
        if refund is None or refund.status is not RefundStatus.PROPOSED:
            raise RefundApprovalError("Only a proposed refund may request approval")
        approval = self.approval_engine.request_if_required(
            session,
            ApprovalContext(action_type=ApprovalActionType.REFUND, amount=refund.amount),
            action_type=ApprovalActionType.REFUND.value,
            resource_type="refund",
            resource_id=refund.id,
            requested_action={"refund_id": str(refund.id), "amount": str(refund.amount)},
            reason=reason,
            requester=requester,
            risk_level="financial",
        )
        if approval is None:
            refund.status = RefundStatus.APPROVED
            session.add(
                AuditEvent(
                    actor_type="system",
                    actor_id="refund-policy",
                    action="refund.auto_approved",
                    resource_type="refund",
                    resource_id=refund.id,
                    after_state={"status": RefundStatus.APPROVED.value},
                    reason="Refund was within the configured approval threshold",
                    correlation_id=refund.order_id,
                )
            )
        else:
            refund.approval_id = approval.id
        return approval


def register_refund_approval_handler(registry: ApprovalActionRegistry) -> None:
    def approve_refund(approval: Approval, session: Session) -> None:
        refund = session.get(Refund, approval.resource_id)
        if refund is None or approval.resource_type != "refund":
            raise RefundApprovalError("Refund approval references missing data")
        if refund.status is not RefundStatus.PROPOSED:
            raise RefundApprovalError("Refund is no longer proposed")
        requested_id = approval.requested_payload.get("refund_id")
        requested_amount = approval.requested_payload.get("amount")
        if requested_id != str(refund.id) or Decimal(requested_amount) != refund.amount:
            raise RefundApprovalError("Refund approval payload does not match the refund")
        refund.status = RefundStatus.APPROVED
        session.add(
            AuditEvent(
                actor_type="human",
                actor_id=approval.decided_by or "unknown",
                action="refund.approved",
                resource_type="refund",
                resource_id=refund.id,
                after_state={"status": RefundStatus.APPROVED.value},
                reason=approval.rationale,
                correlation_id=refund.order_id,
            )
        )

    registry.register(ApprovalActionType.REFUND.value, approve_refund)
