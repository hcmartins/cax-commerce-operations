import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.agents.customer_service_models import CustomerServiceAIResponse
from commerce_operations.agents.listing_models import GeneratedListing, ImageRequirement
from commerce_operations.ai import LLMResult, LLMUsage
from commerce_operations.application.customer_service import CustomerServiceAgent
from commerce_operations.application.listings import (
    MarketplaceListingAgent,
    register_listing_approval_handler,
)
from commerce_operations.application.marketplace_publication import MarketplacePublicationService
from commerce_operations.application.orchestration import AutonomousOrchestrator
from commerce_operations.application.orders import InsufficientInventoryError, OrderService
from commerce_operations.application.refunds import RefundService, register_refund_approval_handler
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.config import Settings
from commerce_operations.events import (
    DatabaseEventStore,
    EventHandlerRegistry,
    EventProcessor,
    create_event,
)
from commerce_operations.events.processor import EventProcessingError
from commerce_operations.events.types import OrderReturnedPayload
from commerce_operations.integrations.marketplaces import (
    ConfiguredMarketplaceListingAdapter,
    MarketplaceConnectorRegistry,
    MarketplaceListingRequirements,
    MarketplaceListingResult,
    MarketplaceValidationResult,
)
from commerce_operations.integrations.marketplaces.orders import (
    NormalizedMarketplaceOrder,
    NormalizedOrderItem,
)
from commerce_operations.main import create_app
from commerce_operations.persistence import Base
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.enums import (
    ApprovalStatus,
    ConversationStatus,
    CustomerIntent,
    CustomerServiceDecision,
    InventoryMovementType,
    OrderStatus,
    PublicationStatus,
    RefundStatus,
    ReturnStatus,
    RunStatus,
)
from commerce_operations.persistence.models import (
    AgentRun,
    AuditEvent,
    CustomerConversation,
    DomainEvent,
    InventoryItem,
    InventoryMovement,
    ListingDraft,
    MarketplaceListing,
    Order,
    ProcurementRequest,
    PurchaseOrder,
    Refund,
    Return,
    WorkflowRun,
)
from commerce_operations.worker import Worker


class ListingLLM:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def generate(self, request, response_model):
        self.calls += 1
        if self.fail:
            raise TimeoutError("mock AI outage")
        return LLMResult(
            output=GeneratedListing(
                title="Insulated reusable water bottle 750ml",
                bullet_points=["Double-wall insulation", "Leak-resistant lid", "Steel body"],
                description="Reusable insulated bottle for hot and cold drinks.",
                search_terms=["water bottle", "insulated bottle"],
                category_suggestion="Drinkware",
                product_attributes={"material": "steel"},
                sku="BOTTLE-BLACK-750",
                proposed_price=Decimal("14.99"),
                image_requirements=[
                    ImageRequirement(image_type="main", description="White background")
                ],
            ),
            provider="mock-ai",
            model="mock-listing",
            usage=LLMUsage(100, 50, Decimal("0.01"), "USD"),
        )


class CustomerLLM:
    def generate(self, request, response_model):
        return LLMResult(
            output=CustomerServiceAIResponse(
                intent=CustomerIntent.ORDER_STATUS,
                decision=CustomerServiceDecision.AUTO_RESPOND,
                risk_level="low",
                generated_response="Your order has been delivered.",
                rationale="Known order state",
            ),
            provider="mock-ai",
            model="mock-support",
            usage=LLMUsage(20, 10, Decimal("0.002"), "USD"),
        )


class SandboxConnector:
    marketplace = "ebay"
    account_id = "sandbox-seller"

    def __init__(self):
        self.calls = 0
        self.outage = False

    def validate_listing(self, listing):
        return MarketplaceValidationResult(True)

    def create_listing(self, listing, *, idempotency_key):
        self.calls += 1
        if self.outage:
            raise ConnectionError("mock marketplace outage")
        return MarketplaceListingResult("sandbox-listing-1", "published")


def approval_engine(settings):
    registry = ApprovalActionRegistry()
    register_listing_approval_handler(registry)
    register_refund_approval_handler(registry)
    return ApprovalEngine(ApprovalPolicy.from_settings(settings), action_registry=registry)


def listing_adapter():
    return ConfiguredMarketplaceListingAdapter(
        MarketplaceListingRequirements(
            marketplace="ebay",
            title_max_length=120,
            bullet_min_count=3,
            bullet_max_count=5,
            bullet_max_length=200,
            description_max_length=2000,
            keyword_max_count=10,
            keyword_max_length=100,
            required_attributes=("material",),
            minimum_required_images=1,
            minimum_price=Decimal("1"),
        )
    )


@pytest.fixture
def platform(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'complete-platform.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        environment="test", refund_approval_threshold=10, database_url="sqlite://", _env_file=None
    )
    app = create_app(settings)

    def sessions():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, factory, settings
    engine.dispose()


def prepare_received_inventory(client, factory, approved_product_payload):
    assert (
        client.post(
            "/api/v1/approved-products",
            json=approved_product_payload,
            headers={"Idempotency-Key": "e2e-approved-product"},
        ).status_code
        == 202
    )
    with factory() as session:
        procurement = session.scalar(select(ProcurementRequest))
        assert procurement is not None
        procurement_id = procurement.id
    submitted = client.post(
        f"/api/v1/procurement-requests/{procurement_id}/submit-for-approval",
        json={"requester": "buyer@test", "reason": "Quote reviewed"},
    )
    assert submitted.status_code == 200
    approval_id = submitted.json()["approval_id"]
    assert (
        client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            json={"approver": "finance@test", "reason": "Budget approved"},
        ).status_code
        == 200
    )
    with factory() as session:
        purchase_order = session.scalar(select(PurchaseOrder))
        assert purchase_order is not None
        purchase_order_id = purchase_order.id
    assert (
        client.post(
            f"/api/v1/procurement-requests/{procurement_id}/order",
            json={"actor": "buyer@test", "reason": "Sent to supplier"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/procurement-requests/{procurement_id}/mark-shipped",
            json={"actor": "buyer@test", "reason": "Tracking received"},
        ).status_code
        == 200
    )
    received = client.post(
        f"/api/v1/purchase-orders/{purchase_order_id}/receive",
        headers={"Idempotency-Key": "e2e-goods-receipt"},
        json={
            "sku": "BOTTLE-BLACK-750",
            "storage_location": "warehouse-a",
            "quantity_received": 100,
            "landed_unit_cost": "5.00",
            "low_stock_threshold": 98,
            "actor": "warehouse@test",
            "reason": "Delivery checked",
        },
    )
    assert received.status_code == 200
    return uuid.UUID(received.json()["inventory"]["id"])


def test_complete_commerce_operations_journey(platform, approved_product_payload):
    client, factory, settings = platform
    inventory_id = prepare_received_inventory(client, factory, approved_product_payload)
    approvals = approval_engine(settings)
    listing_llm = ListingLLM()
    agent = MarketplaceListingAgent(listing_llm, listing_adapter(), approvals)
    listing_handlers = EventHandlerRegistry()
    agent.register(listing_handlers)
    with factory() as session:
        stock_event = session.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "STOCK_RECEIVED")
        )
        assert stock_event is not None
        stock_event_id = stock_event.id
    listing_processor = EventProcessor(factory, listing_handlers)
    assert listing_processor.process(stock_event_id).handlers_processed == 1
    assert listing_processor.process(stock_event_id).handlers_skipped == 1
    assert listing_llm.calls == 1

    with factory() as session:
        draft = session.scalar(select(ListingDraft))
        assert draft is not None and draft.approval_status is ApprovalStatus.PENDING
        listing_approval_id = draft.approval_id
    with factory.begin() as session:
        approvals.approve(
            session, listing_approval_id, approver="marketplace@test", reason="Draft reviewed"
        )

    connector = SandboxConnector()
    connectors = MarketplaceConnectorRegistry()
    connectors.register(connector)
    publication_handlers = EventHandlerRegistry()
    MarketplacePublicationService(connectors).register(publication_handlers)
    with factory() as session:
        approved_event = session.scalar(
            select(DomainEvent).where(DomainEvent.event_type == "LISTING_APPROVED")
        )
        assert approved_event is not None
        approved_event_id = approved_event.id
    publication_processor = EventProcessor(factory, publication_handlers)
    assert publication_processor.process(approved_event_id).handlers_processed == 1
    assert publication_processor.process(approved_event_id).handlers_skipped == 1
    assert connector.calls == 1

    command = NormalizedMarketplaceOrder(
        marketplace="ebay",
        marketplace_account_id="sandbox-seller",
        external_order_id="sandbox-order-1",
        source_event_id="webhook-1",
        status=OrderStatus.PAID,
        currency="GBP",
        total_amount=Decimal("44.97"),
        customer_reference="customer-token",
        shipping_details={"country": "GB"},
        ordered_at=datetime.now(UTC),
        items=(
            NormalizedOrderItem(
                external_line_id="line-1",
                sku="BOTTLE-BLACK-750",
                quantity=3,
                unit_price=Decimal("14.99"),
            ),
        ),
    )
    orders = OrderService()
    with factory.begin() as session:
        first = orders.ingest(session, command)
        order_id = first.order.id
    with factory.begin() as session:
        duplicate = orders.ingest(
            session, command.model_copy(update={"source_event_id": "webhook-2"})
        )
        assert duplicate.duplicate is True and duplicate.order.id == order_id
    with factory.begin() as session:
        orders.update_status(
            session, order_id, OrderStatus.PROCESSING, actor="ops@test", reason="Packed"
        )
        orders.update_status(
            session, order_id, OrderStatus.DISPATCHED, actor="ops@test", reason="Carrier scan"
        )
        orders.update_status(
            session, order_id, OrderStatus.DELIVERED, actor="ops@test", reason="Delivered"
        )

    with factory.begin() as session:
        order = session.get(Order, order_id)
        conversation = CustomerConversation(
            order_id=order.id,
            product_id=order.items[0].inventory_item.product_id,
            marketplace="ebay",
            marketplace_account_id="sandbox-seller",
            external_conversation_id="conversation-1",
            customer_reference="customer-token",
            status=ConversationStatus.OPEN,
        )
        session.add(conversation)
        session.flush()
        message = (
            CustomerServiceAgent(CustomerLLM(), approvals)
            .handle_message(
                session,
                conversation.id,
                external_message_id="question-1",
                channel="ebay-messaging",
                content="Where is my order?",
            )
            .message
        )
        assert message.final_response == "Your order has been delivered."

    with factory.begin() as session:
        order = session.get(Order, order_id)
        returned = Return(
            order_id=order.id,
            order_item_id=order.items[0].id,
            quantity=1,
            reason="Changed mind",
            disposition="sellable",
            status=ReturnStatus.RECEIVED,
        )
        session.add(returned)
        session.flush()
        refund = Refund(
            order_id=order.id,
            return_id=returned.id,
            marketplace="ebay",
            marketplace_account_id="sandbox-seller",
            amount=Decimal("14.99"),
            currency="GBP",
            reason="Accepted return",
            status=RefundStatus.PROPOSED,
        )
        session.add(refund)
        session.flush()
        refund_approval = RefundService(approvals).request_approval(
            session, refund.id, requester="support@test", reason="Refund exceeds threshold"
        )
        assert refund_approval is not None
        refund_approval_id = refund_approval.id
        return_id = returned.id
    approved_refund = client.post(
        f"/api/v1/approvals/{refund_approval_id}/approve",
        json={"approver": "finance@test", "reason": "Return received"},
    )
    assert approved_refund.status_code == 200
    with factory.begin() as session:
        refund = session.scalar(select(Refund))
        assert refund.status is RefundStatus.APPROVED
        event = DatabaseEventStore().publish(
            session,
            create_event(
                OrderReturnedPayload(order_id=order_id, return_id=return_id, quantity=1),
                aggregate_type="order",
                aggregate_id=order_id,
                aggregate_version=2,
                idempotency_key=f"order-returned:{return_id}",
            ),
        )
        return_event_id = event.id
    return_handlers = EventHandlerRegistry()
    AutonomousOrchestrator(settings=settings).register(return_handlers)
    assert EventProcessor(factory, return_handlers).process(return_event_id).handlers_processed == 1

    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        listing = session.scalar(select(MarketplaceListing))
        order = session.get(Order, order_id)
        returned = session.get(Return, return_id)
        assert inventory.quantity_on_hand == 98 and inventory.reserved_quantity == 0
        assert listing.publication_status is PublicationStatus.PUBLISHED
        assert order.status is OrderStatus.RETURNED
        assert returned.status is ReturnStatus.COMPLETED
        assert session.scalar(select(func.count()).select_from(Order)) == 1
        assert session.scalar(select(func.count()).select_from(MarketplaceListing)) == 1
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "LOW_STOCK")
            )
            >= 1
        )
        assert session.scalar(select(func.count()).select_from(AuditEvent)) >= 15


def test_ai_and_marketplace_outages_are_retry_safe(platform, approved_product_payload):
    client, factory, settings = platform
    prepare_received_inventory(client, factory, approved_product_payload)
    with factory() as session:
        stock_event_id = session.scalar(
            select(DomainEvent.id).where(DomainEvent.event_type == "STOCK_RECEIVED")
        )
    failing_llm = ListingLLM(fail=True)
    handlers = EventHandlerRegistry()
    MarketplaceListingAgent(failing_llm, listing_adapter(), approval_engine(settings)).register(
        handlers
    )
    with pytest.raises(EventProcessingError):
        EventProcessor(factory, handlers).process(stock_event_id)
    with factory() as session:
        event = session.get(DomainEvent, stock_event_id)
        assert event.last_error and "TimeoutError" in event.last_error
        assert session.scalar(select(func.count()).select_from(ListingDraft)) == 0
        assert session.scalar(select(func.count()).select_from(AgentRun)) == 0

    failing_llm.fail = False
    assert EventProcessor(factory, handlers).process(stock_event_id).handlers_processed == 1
    approvals = approval_engine(settings)
    with factory.begin() as session:
        draft = session.scalar(select(ListingDraft))
        approvals.approve(
            session,
            draft.approval_id,
            approver="marketplace@test",
            reason="Content reviewed",
        )
    with factory() as session:
        approved_event_id = session.scalar(
            select(DomainEvent.id).where(DomainEvent.event_type == "LISTING_APPROVED")
        )
    connector = SandboxConnector()
    connector.outage = True
    connectors = MarketplaceConnectorRegistry()
    connectors.register(connector)
    publication_handlers = EventHandlerRegistry()
    MarketplacePublicationService(connectors).register(publication_handlers)
    publication_processor = EventProcessor(factory, publication_handlers)
    with pytest.raises(EventProcessingError):
        publication_processor.process(approved_event_id)
    with factory() as session:
        event = session.get(DomainEvent, approved_event_id)
        assert event.last_error and "ConnectionError" in event.last_error
        assert session.scalar(select(func.count()).select_from(MarketplaceListing)) == 0
    connector.outage = False
    assert publication_processor.process(approved_event_id).handlers_processed == 1
    with factory() as session:
        listing = session.scalar(select(MarketplaceListing))
        assert listing.publication_status is PublicationStatus.PUBLISHED
        assert connector.calls == 2


def test_failed_order_transaction_rolls_back_every_business_action(
    platform, approved_product_payload
):
    client, factory, _ = platform
    inventory_id = prepare_received_inventory(client, factory, approved_product_payload)
    impossible = NormalizedMarketplaceOrder(
        marketplace="ebay",
        marketplace_account_id="sandbox",
        external_order_id="too-large",
        source_event_id="too-large-event",
        status=OrderStatus.PAID,
        currency="GBP",
        total_amount=Decimal("1499"),
        customer_reference=None,
        shipping_details={},
        ordered_at=datetime.now(UTC),
        items=(
            NormalizedOrderItem(
                external_line_id="line",
                sku="BOTTLE-BLACK-750",
                quantity=101,
                unit_price=Decimal("14.99"),
            ),
        ),
    )
    with pytest.raises(InsufficientInventoryError), factory.begin() as session:
        OrderService().ingest(session, impossible)
    with factory() as session:
        inventory = session.get(InventoryItem, inventory_id)
        assert inventory.quantity_on_hand == 100 and inventory.reserved_quantity == 0
        assert session.scalar(select(func.count()).select_from(Order)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(InventoryMovement)
                .where(InventoryMovement.movement_type == InventoryMovementType.RESERVATION)
            )
            == 0
        )


def test_delayed_worker_delivery_accepts_valid_advanced_procurement_state(
    platform, approved_product_payload, tmp_path
):
    client, factory, settings = platform
    prepare_received_inventory(client, factory, approved_product_payload)

    worker_settings = settings.model_copy(
        update={"worker_heartbeat_path": str(tmp_path / "delayed-worker-heartbeat")}
    )
    assert Worker(worker_settings, factory).run_once() >= 2

    with factory() as session:
        orchestration_runs = session.scalars(
            select(WorkflowRun).where(WorkflowRun.workflow_name.like("orchestrator:%"))
        ).all()
        assert len(orchestration_runs) >= 2
        assert all(run.status is RunStatus.SUCCEEDED for run in orchestration_runs)
