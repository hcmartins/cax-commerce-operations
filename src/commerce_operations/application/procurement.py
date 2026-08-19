import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalActionType, ApprovalContext
from commerce_operations.domains.procurement import transition
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    ProcurementRequestedPayload,
    ProcurementStatusChangedPayload,
    PurchaseApprovedPayload,
    PurchaseOrderCreatedPayload,
    create_event,
)
from commerce_operations.persistence.enums import (
    ProcurementStatus,
    PurchaseOrderStatus,
    RunStatus,
)
from commerce_operations.persistence.models import (
    Approval,
    AuditEvent,
    ProcurementRequest,
    PurchaseOrder,
    SupplierQuote,
    WorkflowRun,
)


class ProcurementNotFoundError(LookupError):
    pass


class PurchaseOrderNotFoundError(LookupError):
    pass


class ProcurementValidationError(ValueError):
    pass


class ProcurementService:
    def __init__(self, event_store: DatabaseEventStore | None = None) -> None:
        self.event_store = event_store or DatabaseEventStore()

    def list(self, session: Session, *, limit: int = 100) -> Sequence[ProcurementRequest]:
        return session.scalars(
            select(ProcurementRequest).order_by(ProcurementRequest.created_at.desc()).limit(limit)
        ).all()

    def get(
        self, session: Session, procurement_id: uuid.UUID, *, for_update: bool = False
    ) -> ProcurementRequest:
        statement = select(ProcurementRequest).where(ProcurementRequest.id == procurement_id)
        if for_update:
            statement = statement.with_for_update()
        request = session.scalar(statement)
        if request is None:
            raise ProcurementNotFoundError(str(procurement_id))
        return request

    def submit_for_approval(
        self,
        session: Session,
        procurement_id: uuid.UUID,
        *,
        requester: str,
        reason: str,
        approval_engine: ApprovalEngine,
    ) -> Approval:
        request = self.get(session, procurement_id, for_update=True)
        quote = session.get(SupplierQuote, request.selected_quote_id)
        if quote is None:
            raise ProcurementNotFoundError("Selected supplier quote is missing")
        if request.requested_quantity < quote.moq:
            raise ProcurementValidationError(
                f"Requested quantity {request.requested_quantity} is below MOQ {quote.moq}"
            )
        self._apply_transition(
            session, request, ProcurementStatus.AWAITING_APPROVAL, requester, reason
        )
        approval = approval_engine.request_if_required(
            session,
            ApprovalContext(
                ApprovalActionType.SUPPLIER_PURCHASE,
                amount=self._purchase_total(quote, request.requested_quantity),
                risk_level="financial",
            ),
            action_type=ApprovalActionType.SUPPLIER_PURCHASE.value,
            resource_type="procurement_request",
            resource_id=request.id,
            requested_action={
                "operation": "create_purchase_order",
                "supplier_id": str(quote.supplier_id),
                "quote_id": str(quote.id),
                "quantity": request.requested_quantity,
                "unit_cost": str(quote.unit_cost),
                "shipping_cost": str(quote.shipping_cost),
                "total_amount": str(self._purchase_total(quote, request.requested_quantity)),
                "currency": quote.currency,
            },
            reason=reason,
            requester=requester,
            risk_level="financial",
            workflow_run_id=request.workflow_run_id,
        )
        if approval is None:
            raise RuntimeError("Supplier purchase policy unexpectedly allowed the purchase")

        workflow = self._workflow(session, request)
        event = create_event(
            ProcurementRequestedPayload(
                procurement_request_id=request.id,
                product_id=request.product_id,
                quantity=request.requested_quantity,
            ),
            aggregate_type="procurement_request",
            aggregate_id=request.id,
            aggregate_version=request.version,
            correlation_id=workflow.correlation_id,
            workflow_id=workflow.id,
            idempotency_key=f"procurement-requested:{request.id}",
        )
        self.event_store.publish(session, event)
        workflow.current_step = "awaiting_purchase_approval"
        return approval

    def approve_purchase(self, approval: Approval, session: Session) -> None:
        if approval.resource_type != "procurement_request":
            raise RuntimeError("Supplier purchase approval references the wrong entity type")
        request = self.get(session, approval.resource_id, for_update=True)
        self._apply_transition(
            session,
            request,
            ProcurementStatus.APPROVED,
            approval.decided_by or "approval-engine",
            approval.rationale or "Purchase approved",
        )
        workflow = self._workflow(session, request)
        self.event_store.publish(
            session,
            create_event(
                PurchaseApprovedPayload(
                    procurement_request_id=request.id,
                    approval_id=approval.id,
                ),
                aggregate_type="procurement_request",
                aggregate_id=request.id,
                aggregate_version=request.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"purchase-approved:{request.id}",
            ),
        )
        self._create_purchase_order(session, request, approval)
        workflow.current_step = "purchase_order_created"

    def mark_ordered(
        self,
        session: Session,
        procurement_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
        external_reference: str | None = None,
        now: datetime | None = None,
    ) -> ProcurementRequest:
        request = self.get(session, procurement_id, for_update=True)
        purchase_order = self._purchase_order(session, request.id)
        now = now or datetime.now(UTC)
        self._apply_transition(session, request, ProcurementStatus.ORDERED, actor, reason)
        purchase_order.status = PurchaseOrderStatus.SUBMITTED
        purchase_order.ordered_at = now
        purchase_order.external_reference = external_reference
        return request

    def mark_shipped(
        self,
        session: Session,
        procurement_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
        expected_arrival_at: datetime | None = None,
        now: datetime | None = None,
    ) -> ProcurementRequest:
        request = self.get(session, procurement_id, for_update=True)
        purchase_order = self._purchase_order(session, request.id)
        now = now or datetime.now(UTC)
        self._apply_transition(session, request, ProcurementStatus.SHIPPED, actor, reason)
        purchase_order.status = PurchaseOrderStatus.SHIPPED
        purchase_order.shipped_at = now
        if expected_arrival_at is not None:
            request.expected_arrival_at = expected_arrival_at
            purchase_order.expected_arrival_at = expected_arrival_at
        return request

    def mark_received(
        self,
        session: Session,
        procurement_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> ProcurementRequest:
        request = self.get(session, procurement_id, for_update=True)
        purchase_order = self._purchase_order(session, request.id)
        now = now or datetime.now(UTC)
        self._apply_transition(session, request, ProcurementStatus.RECEIVED, actor, reason)
        purchase_order.status = PurchaseOrderStatus.RECEIVED
        purchase_order.received_at = now
        workflow = self._workflow(session, request)
        workflow.status = RunStatus.SUCCEEDED
        workflow.current_step = "goods_received"
        return request

    def cancel(
        self,
        session: Session,
        procurement_id: uuid.UUID,
        *,
        actor: str,
        reason: str,
    ) -> ProcurementRequest:
        request = self.get(session, procurement_id, for_update=True)
        self._apply_transition(session, request, ProcurementStatus.CANCELLED, actor, reason)
        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.procurement_request_id == procurement_id)
        )
        if purchase_order is not None:
            purchase_order.status = PurchaseOrderStatus.CANCELLED
        workflow = self._workflow(session, request)
        workflow.status = RunStatus.CANCELLED
        workflow.current_step = "cancelled"
        return request

    def _create_purchase_order(
        self, session: Session, request: ProcurementRequest, approval: Approval
    ) -> PurchaseOrder:
        existing = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.procurement_request_id == request.id)
        )
        if existing is not None:
            return existing
        quote = session.get(SupplierQuote, request.selected_quote_id)
        if quote is None:
            raise ProcurementNotFoundError("Selected supplier quote is missing")
        purchase_order = PurchaseOrder(
            procurement_request_id=request.id,
            supplier_id=quote.supplier_id,
            po_number=f"PO-{request.id.hex[:12].upper()}",
            quantity=request.requested_quantity,
            total_amount=self._purchase_total(quote, request.requested_quantity),
            currency=quote.currency,
            terms={"approval_id": str(approval.id)},
            status=PurchaseOrderStatus.APPROVED,
            expected_arrival_at=request.expected_arrival_at,
        )
        session.add(purchase_order)
        session.flush()
        workflow = self._workflow(session, request)
        self.event_store.publish(
            session,
            create_event(
                PurchaseOrderCreatedPayload(
                    purchase_order_id=purchase_order.id,
                    procurement_request_id=request.id,
                    po_number=purchase_order.po_number,
                ),
                aggregate_type="purchase_order",
                aggregate_id=purchase_order.id,
                aggregate_version=purchase_order.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                causation_id=None,
                idempotency_key=f"purchase-order-created:{request.id}",
            ),
        )
        return purchase_order

    def _apply_transition(
        self,
        session: Session,
        request: ProcurementRequest,
        target: ProcurementStatus,
        actor: str,
        reason: str,
    ) -> None:
        change = transition(request.status, target)
        request.status = target
        session.flush()
        session.add(
            AuditEvent(
                actor_type="user" if actor != "system" else "system",
                actor_id=actor,
                action="procurement.status_changed",
                resource_type="procurement_request",
                resource_id=request.id,
                before_state={"status": change.previous.value},
                after_state={"status": change.current.value},
                reason=reason,
                correlation_id=self._workflow(session, request).correlation_id,
            )
        )
        workflow = self._workflow(session, request)
        self.event_store.publish(
            session,
            create_event(
                ProcurementStatusChangedPayload(
                    procurement_request_id=request.id,
                    previous_status=change.previous.value,
                    current_status=change.current.value,
                    actor=actor,
                ),
                aggregate_type="procurement_request",
                aggregate_id=request.id,
                aggregate_version=request.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"procurement-status:{request.id}:{target.value}",
            ),
        )

    @staticmethod
    def _purchase_total(quote: SupplierQuote, quantity: int) -> Decimal:
        return quote.unit_cost * quantity + quote.shipping_cost

    @staticmethod
    def _workflow(session: Session, request: ProcurementRequest) -> WorkflowRun:
        workflow = session.get(WorkflowRun, request.workflow_run_id)
        if workflow is None:
            raise ProcurementNotFoundError("Procurement workflow is missing")
        return workflow

    @staticmethod
    def _purchase_order(session: Session, procurement_id: uuid.UUID) -> PurchaseOrder:
        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.procurement_request_id == procurement_id)
        )
        if purchase_order is None:
            raise PurchaseOrderNotFoundError(str(procurement_id))
        return purchase_order


def register_procurement_approval_handler(registry: ApprovalActionRegistry) -> None:
    service = ProcurementService()
    registry.register(
        ApprovalActionType.SUPPLIER_PURCHASE.value,
        service.approve_purchase,
    )
