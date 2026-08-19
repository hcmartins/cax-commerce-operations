from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.application.customer_service import CustomerServiceAgent
from commerce_operations.application.inventory import InventoryService
from commerce_operations.application.listings import MarketplaceListingAgent
from commerce_operations.application.marketplace_publication import MarketplacePublicationService
from commerce_operations.application.orders import OrderService
from commerce_operations.config import Settings
from commerce_operations.events.handlers import EventHandlerRegistry
from commerce_operations.events.types import (
    CustomerMessageReceivedPayload,
    EventEnvelope,
    EventPayload,
    EventType,
    ListingApprovedPayload,
    LowStockPayload,
    OrderReceivedPayload,
    OrderReturnedPayload,
    ProductApprovedPayload,
    PurchaseApprovedPayload,
    StockReceivedPayload,
)
from commerce_operations.persistence.enums import (
    ApprovalStatus,
    InventoryMovementType,
    OrderStatus,
    ProcurementStatus,
    RefundStatus,
    ReturnStatus,
)
from commerce_operations.persistence.models import (
    Approval,
    AuditEvent,
    InventoryItem,
    InventoryMovement,
    MarketplaceListing,
    Order,
    ProcurementRequest,
    PurchaseOrder,
    Refund,
    ReorderRecommendation,
    Return,
)
from commerce_operations.workflows import (
    NonRetryableWorkflowError,
    WorkflowDefinition,
    WorkflowEngine,
    WorkflowExecutionPolicy,
    WorkflowOutcome,
)


class AutonomousOrchestrator:
    """Event routing only; business work remains in specialist services."""

    routes = {
        EventType.PRODUCT_APPROVED: "product-approved-to-procurement",
        EventType.PURCHASE_APPROVED: "purchase-approved-to-purchase-order",
        EventType.STOCK_RECEIVED: "stock-received-to-listing",
        EventType.LISTING_APPROVED: "listing-approved-to-publication",
        EventType.ORDER_RECEIVED: "order-received-to-fulfilment",
        EventType.LOW_STOCK: "low-stock-to-reorder-recommendation",
        EventType.CUSTOMER_MESSAGE_RECEIVED: "customer-message-to-service-agent",
        EventType.ORDER_RETURNED: "order-returned-to-reconciliation",
    }

    def __init__(
        self,
        *,
        settings: Settings,
        listing_agents: Sequence[MarketplaceListingAgent] = (),
        publication_service: MarketplacePublicationService | None = None,
        customer_service_agent: CustomerServiceAgent | None = None,
        engine: WorkflowEngine | None = None,
        inventory_service: InventoryService | None = None,
        order_service: OrderService | None = None,
    ) -> None:
        self.settings = settings
        self.listing_agents = tuple(listing_agents)
        self.publication_service = publication_service
        self.customer_service_agent = customer_service_agent
        self.engine = engine or WorkflowEngine()
        self.inventory_service = inventory_service or InventoryService()
        self.order_service = order_service or OrderService()
        policy = WorkflowExecutionPolicy(
            max_attempts=settings.workflow_max_attempts,
            timeout_seconds=settings.workflow_timeout_seconds,
            retry_delay_seconds=settings.workflow_retry_delay_seconds,
        )
        steps = {
            "product-approved-to-procurement": self._product_to_procurement,
            "purchase-approved-to-purchase-order": self._purchase_to_order,
            "stock-received-to-listing": self._stock_to_listing,
            "listing-approved-to-publication": self._listing_to_publication,
            "order-received-to-fulfilment": self._order_to_fulfilment,
            "low-stock-to-reorder-recommendation": self._low_stock_to_reorder,
            "customer-message-to-service-agent": self._message_to_customer_service,
            "order-returned-to-reconciliation": self._return_to_reconciliation,
        }
        for name, step in steps.items():
            self.engine.register(WorkflowDefinition(name, 1, step, policy))

    def register(
        self,
        registry: EventHandlerRegistry,
        event_types: set[EventType] | None = None,
    ) -> None:
        for event_type, definition_name in self.routes.items():
            if event_types is not None and event_type not in event_types:
                continue
            registry.register(
                event_type,
                f"orchestrator:{definition_name}",
                self._event_handler(definition_name),
            )

    def _event_handler(self, definition_name):
        def handle(event: EventEnvelope[EventPayload], session: Session) -> None:
            self.engine.handle_event(definition_name, event, session)

        return handle

    @staticmethod
    def _product_to_procurement(event, workflow, session):
        payload = ProductApprovedPayload.model_validate(event.data)
        request = (
            session.get(ProcurementRequest, payload.procurement_request_id)
            if payload.procurement_request_id
            else session.scalar(
                select(ProcurementRequest).where(
                    ProcurementRequest.product_id == payload.product_id
                )
            )
        )
        valid_states = {
            ProcurementStatus.PROPOSED,
            ProcurementStatus.AWAITING_APPROVAL,
            ProcurementStatus.APPROVED,
            ProcurementStatus.ORDERED,
            ProcurementStatus.SHIPPED,
            ProcurementStatus.RECEIVED,
        }
        if request is None or request.status not in valid_states:
            raise NonRetryableWorkflowError(
                "PRODUCT_APPROVED is missing its active procurement request"
            )
        if request.selected_quote is None:
            raise NonRetryableWorkflowError("Procurement request has no selected supplier quote")
        return WorkflowOutcome.completed(
            "procurement_proposed", procurement_request_id=str(request.id)
        )

    @staticmethod
    def _purchase_to_order(event, workflow, session):
        payload = PurchaseApprovedPayload.model_validate(event.data)
        request = session.get(ProcurementRequest, payload.procurement_request_id)
        purchase_order = session.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.procurement_request_id == payload.procurement_request_id
            )
        )
        valid_states = {
            ProcurementStatus.APPROVED,
            ProcurementStatus.ORDERED,
            ProcurementStatus.SHIPPED,
            ProcurementStatus.RECEIVED,
        }
        if request is None or request.status not in valid_states:
            raise NonRetryableWorkflowError(
                "PURCHASE_APPROVED references a request without a valid approved history"
            )
        if purchase_order is None:
            raise NonRetryableWorkflowError("Approved purchase is missing its purchase order")
        return WorkflowOutcome.completed(
            "purchase_order_created", purchase_order_id=str(purchase_order.id)
        )

    def _stock_to_listing(self, event, workflow, session):
        payload = StockReceivedPayload.model_validate(event.data)
        if not self.listing_agents:
            raise NonRetryableWorkflowError("No marketplace listing agents are configured")
        draft_ids = []
        for agent in self.listing_agents:
            draft = agent.generate(
                session,
                inventory_item_id=payload.inventory_item_id,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                source_event_id=event.event_id,
            )
            draft_ids.append(str(draft.id))
        return WorkflowOutcome.completed("listing_drafts_created", listing_draft_ids=draft_ids)

    def _listing_to_publication(self, event, workflow, session):
        if self.publication_service is None:
            raise NonRetryableWorkflowError("Marketplace publication service is not configured")
        payload = ListingApprovedPayload.model_validate(event.data)
        self.publication_service.handle_listing_approved(event, session)
        listing = session.scalar(
            select(MarketplaceListing).where(
                MarketplaceListing.draft_id == payload.listing_draft_id
            )
        )
        if listing is None or listing.external_listing_id is None:
            raise NonRetryableWorkflowError("Approved listing was not published")
        return WorkflowOutcome.completed(
            "listing_published", marketplace_listing_id=str(listing.id)
        )

    @staticmethod
    def _order_to_fulfilment(event, workflow, session):
        payload = OrderReceivedPayload.model_validate(event.data)
        order = session.get(Order, payload.order_id)
        if order is None or not order.items or order.workflow_run is None:
            raise NonRetryableWorkflowError(
                "ORDER_RECEIVED is missing order items or its fulfilment workflow"
            )
        if order.status in {OrderStatus.PENDING, OrderStatus.PAID, OrderStatus.PROCESSING}:
            for item in order.items:
                if not item.reservation_reference:
                    raise NonRetryableWorkflowError("Order item has no inventory reservation")
                movement = session.scalar(
                    select(InventoryMovement).where(
                        InventoryMovement.source_id == str(order.id),
                        InventoryMovement.inventory_item_id == item.inventory_item_id,
                        InventoryMovement.movement_type == InventoryMovementType.RESERVATION,
                    )
                )
                if movement is None:
                    raise NonRetryableWorkflowError("Order reservation ledger entry is missing")
        return WorkflowOutcome.completed(
            "fulfilment_ready", fulfilment_workflow_id=str(order.workflow_run_id)
        )

    def _low_stock_to_reorder(self, event, workflow, session):
        payload = LowStockPayload.model_validate(event.data)
        existing = session.scalar(
            select(ReorderRecommendation).where(
                ReorderRecommendation.source_event_id == event.event_id
            )
        )
        if existing is not None:
            return WorkflowOutcome.completed(
                "reorder_recommended", reorder_recommendation_id=str(existing.id)
            )
        inventory = session.get(InventoryItem, payload.inventory_item_id)
        if inventory is None or inventory.sku != payload.sku:
            raise NonRetryableWorkflowError("LOW_STOCK references missing or mismatched inventory")
        if inventory.available_quantity > inventory.low_stock_threshold:
            raise NonRetryableWorkflowError("Inventory is no longer below its reorder threshold")
        target = max(
            inventory.low_stock_threshold * self.settings.reorder_target_multiplier,
            inventory.low_stock_threshold + 1,
        )
        recommendation = ReorderRecommendation(
            inventory_item_id=inventory.id,
            workflow_run_id=workflow.id,
            source_event_id=event.event_id,
            available_quantity=inventory.available_quantity,
            low_stock_threshold=inventory.low_stock_threshold,
            suggested_quantity=max(target - inventory.available_quantity, 1),
            reason="Available stock reached the configured low-stock threshold",
            status="proposed",
        )
        session.add(recommendation)
        session.flush()
        return WorkflowOutcome.completed(
            "reorder_recommended", reorder_recommendation_id=str(recommendation.id)
        )

    def _message_to_customer_service(self, event, workflow, session):
        if self.customer_service_agent is None:
            raise NonRetryableWorkflowError("Customer service agent is not configured")
        payload = CustomerMessageReceivedPayload.model_validate(event.data)
        message = self.customer_service_agent.process_received_event(event, session)
        if message.classification is None:
            raise NonRetryableWorkflowError("Customer message was not safely classified")
        checkpoints = {
            "customer_message_id": str(payload.message_id),
            "classification": message.classification.value,
        }
        if message.approval_id is not None:
            approval = session.get(Approval, message.approval_id)
            if approval is None:
                raise NonRetryableWorkflowError("Customer response approval is missing")
            if approval.status is ApprovalStatus.APPROVED:
                return WorkflowOutcome.completed("customer_response_approved", **checkpoints)
            approval.workflow_run_id = workflow.id
            return WorkflowOutcome.waiting_for_approval(
                "awaiting_customer_response_approval",
                message.approval_id,
                **checkpoints,
            )
        return WorkflowOutcome.completed("customer_message_classified", **checkpoints)

    def _return_to_reconciliation(self, event, workflow, session):
        payload = OrderReturnedPayload.model_validate(event.data)
        return_record = session.get(Return, payload.return_id)
        order = session.get(Order, payload.order_id)
        if return_record is None or order is None or return_record.order_id != order.id:
            raise NonRetryableWorkflowError("ORDER_RETURNED references missing return/order data")
        if return_record.order_item is None or return_record.disposition not in {
            "sellable",
            "damaged",
            "discarded",
        }:
            raise NonRetryableWorkflowError(
                "Return requires an order item and known inventory disposition"
            )
        if payload.quantity != return_record.quantity:
            raise NonRetryableWorkflowError(
                "Return event quantity does not match the return record"
            )
        if return_record.quantity > return_record.order_item.quantity:
            raise NonRetryableWorkflowError("Returned quantity exceeds the ordered quantity")
        if return_record.disposition == "sellable":
            self.inventory_service.adjust(
                session,
                return_record.order_item.inventory_item_id,
                quantity_delta=return_record.quantity,
                reason=f"Sellable return {return_record.id}",
                actor="system",
                idempotency_key=f"return-receipt:{return_record.id}",
            )
        refunds = session.scalars(select(Refund).where(Refund.order_id == order.id)).all()
        if any(refund.currency != order.currency for refund in refunds):
            raise NonRetryableWorkflowError("Refund and order currencies do not match")
        refunded = sum(
            (
                refund.amount
                for refund in refunds
                if refund.status
                in {RefundStatus.APPROVED, RefundStatus.PROCESSING, RefundStatus.COMPLETED}
            ),
            Decimal("0"),
        )
        if refunded > order.total_amount:
            raise NonRetryableWorkflowError("Refund total exceeds the order total")
        if order.status is not OrderStatus.RETURNED:
            self.order_service.update_status(
                session,
                order.id,
                OrderStatus.RETURNED,
                actor="system",
                reason="Marketplace return reconciliation",
            )
        return_record.status = ReturnStatus.COMPLETED
        session.add(
            AuditEvent(
                actor_type="system",
                actor_id="return-reconciliation",
                action="return.reconciled",
                resource_type="return",
                resource_id=return_record.id,
                after_state={
                    "disposition": return_record.disposition,
                    "inventory_restocked": return_record.disposition == "sellable",
                    "refunded_amount": str(refunded),
                },
                reason="Inventory and financial return reconciliation",
                correlation_id=workflow.correlation_id,
            )
        )
        return WorkflowOutcome.completed(
            "return_reconciled",
            return_id=str(return_record.id),
            refunded_amount=str(refunded),
        )
