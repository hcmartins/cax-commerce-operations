import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.agents.listing_models import GeneratedListing, ImageRequirement
from commerce_operations.ai import LLMResult, LLMUsage
from commerce_operations.application.listings import (
    MarketplaceListingAgent,
    register_listing_approval_handler,
)
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.config import Settings
from commerce_operations.events import (
    DatabaseEventStore,
    EventHandlerRegistry,
    EventProcessor,
    create_event,
)
from commerce_operations.events.types import StockReceivedPayload
from commerce_operations.integrations.marketplaces import (
    ConfiguredMarketplaceListingAdapter,
    MarketplaceListingRequirements,
)
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import ApprovalStatus, RunStatus
from commerce_operations.persistence.models import (
    AgentRun,
    Approval,
    DomainEvent,
    InventoryItem,
    ListingDraft,
    Product,
    WorkflowRun,
)


class MockStructuredLLM:
    def __init__(self, output: GeneratedListing) -> None:
        self.output = output
        self.requests = []

    def generate(self, request, response_model):
        self.requests.append(request)
        assert response_model is GeneratedListing
        return LLMResult(
            output=self.output,
            provider="mock-provider",
            model="mock-listing-model",
            usage=LLMUsage(
                input_tokens=120,
                output_tokens=80,
                cost_amount=Decimal("0.0042"),
                cost_currency="USD",
            ),
            provider_metadata={"request_id": "mock-request-1"},
        )


@pytest.fixture
def listing_session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'listing.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="commerce-intelligence",
            external_product_id="listing-product",
            source_workflow_run_id="source-run",
            source_recommendation_id="source-recommendation",
            source_payload_hash="listing-payload-hash",
            name="Insulated Bottle",
            attributes={"material": "stainless steel", "colour": "black"},
        )
        session.add(product)
        session.flush()
        session.add(
            InventoryItem(
                product_id=product.id,
                sku="BOTTLE-BLACK-750",
                storage_location="warehouse-a",
                quantity_on_hand=100,
                reserved_quantity=0,
                cost_basis=Decimal("5.00"),
                currency="GBP",
                low_stock_threshold=10,
            )
        )
    yield factory
    engine.dispose()


def valid_listing() -> GeneratedListing:
    return GeneratedListing(
        title="Insulated Stainless Steel Water Bottle 750ml",
        bullet_points=[
            "Double-wall insulation",
            "Durable stainless steel",
            "Leak-resistant lid",
        ],
        description="A reusable insulated bottle for hot and cold drinks.",
        search_terms=["water bottle", "insulated bottle", "750ml bottle"],
        category_suggestion="Home & Kitchen > Drinkware",
        product_attributes={"material": "stainless steel", "capacity": "750 ml"},
        sku="BOTTLE-BLACK-750",
        proposed_price=Decimal("14.99"),
        image_requirements=[
            ImageRequirement(
                image_type="main",
                description="Product on a pure white background",
            ),
            ImageRequirement(
                image_type="lifestyle",
                description="Bottle in outdoor use",
            ),
        ],
    )


def adapter(*, title_max_length: int = 120):
    return ConfiguredMarketplaceListingAdapter(
        MarketplaceListingRequirements(
            marketplace="test-marketplace",
            title_max_length=title_max_length,
            bullet_min_count=3,
            bullet_max_count=5,
            bullet_max_length=200,
            description_max_length=2000,
            keyword_max_count=10,
            keyword_max_length=100,
            required_attributes=("material",),
            minimum_required_images=2,
            minimum_price=Decimal("1.00"),
            generation_guidance=("Use plain language",),
        )
    )


def approval_engine() -> ApprovalEngine:
    registry = ApprovalActionRegistry()
    register_listing_approval_handler(registry)
    settings = Settings(environment="test", _env_file=None)
    return ApprovalEngine(
        ApprovalPolicy.from_settings(settings),
        action_registry=registry,
    )


def publish_stock_event(
    factory, *, aggregate_version: int | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    with factory.begin() as session:
        inventory = session.scalar(select(InventoryItem))
        assert inventory is not None
        event = create_event(
            StockReceivedPayload(
                inventory_item_id=inventory.id,
                purchase_order_id=uuid.uuid4(),
                quantity=100,
            ),
            aggregate_type="inventory_item",
            aggregate_id=inventory.id,
            aggregate_version=aggregate_version or inventory.version,
            correlation_id=uuid.uuid4(),
            idempotency_key=f"test-stock-received:{uuid.uuid4()}",
        )
        record = DatabaseEventStore().publish(session, event)
        return record.id, inventory.id


def test_stock_received_generates_valid_versioned_draft_and_human_approval(
    listing_session_factory,
) -> None:
    llm = MockStructuredLLM(valid_listing())
    approvals = approval_engine()
    agent = MarketplaceListingAgent(
        llm,
        adapter(),
        approvals,
        prompt_version="listing-v7",
    )
    handlers = EventHandlerRegistry()
    agent.register(handlers)
    processor = EventProcessor(listing_session_factory, handlers)
    event_id, _ = publish_stock_event(listing_session_factory)

    first = processor.process(event_id)
    duplicate = processor.process(event_id)

    assert first.handlers_processed == 1
    assert duplicate.handlers_skipped == 1
    assert len(llm.requests) == 1
    assert llm.requests[0].constraints["title_max_length"] == 120
    assert llm.requests[0].constraints["generation_guidance"] == ("Use plain language",)

    with listing_session_factory() as session:
        draft = session.scalar(select(ListingDraft))
        assert draft is not None
        assert draft.listing_version == 1
        assert draft.approval_status is ApprovalStatus.PENDING
        assert draft.prompt_version == "listing-v7"
        assert draft.ai_provider == "mock-provider"
        assert draft.ai_model == "mock-listing-model"
        assert draft.validation_results == {"valid": True, "issues": []}
        assert draft.structured_ai_response["sku"] == "BOTTLE-BLACK-750"
        assert draft.marketplace_payload["category"] == "Home & Kitchen > Drinkware"
        assert draft.approval_id is not None
        approval_id = draft.approval_id
        draft_id = draft.id
        agent_run = session.get(AgentRun, draft.agent_run_id)
        assert agent_run is not None
        assert agent_run.input_tokens == 120
        assert agent_run.output_tokens == 80
        assert agent_run.cost_amount == Decimal("0.0042")

    with listing_session_factory.begin() as session:
        approvals.approve(
            session,
            approval_id,
            approver="marketplace-manager@example.com",
            reason="Content and price reviewed",
        )

    with listing_session_factory() as session:
        draft = session.get(ListingDraft, draft_id)
        assert draft is not None and draft.approval_status is ApprovalStatus.APPROVED
        workflow = session.get(WorkflowRun, draft.workflow_run_id)
        assert workflow is not None
        assert workflow.status is RunStatus.RUNNING
        assert workflow.current_step == "listing_approved"
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "LISTING_APPROVED")
            )
            == 1
        )


def test_invalid_ai_draft_is_stored_but_not_sent_for_approval(
    listing_session_factory,
) -> None:
    invalid = valid_listing().model_copy(
        update={
            "title": "X" * 60,
            "sku": "HALLUCINATED-SKU",
            "product_attributes": {},
            "image_requirements": [],
        }
    )
    llm = MockStructuredLLM(invalid)
    agent = MarketplaceListingAgent(llm, adapter(title_max_length=50), approval_engine())
    handlers = EventHandlerRegistry()
    agent.register(handlers)
    event_id, _ = publish_stock_event(listing_session_factory)

    EventProcessor(listing_session_factory, handlers).process(event_id)

    with listing_session_factory() as session:
        draft = session.scalar(select(ListingDraft))
        assert draft is not None
        assert draft.approval_status is ApprovalStatus.REJECTED
        assert draft.validation_results["valid"] is False
        issue_codes = {issue["code"] for issue in draft.validation_results["issues"]}
        assert {"max_length", "sku_mismatch", "required_attribute", "image_count"} <= issue_codes
        assert session.scalar(select(func.count()).select_from(Approval)) == 0
        workflow = session.get(WorkflowRun, draft.workflow_run_id)
        assert workflow is not None and workflow.status is RunStatus.FAILED
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "LISTING_VALIDATION_FAILED")
            )
            == 1
        )


def test_separate_stock_events_create_incrementing_listing_versions(
    listing_session_factory,
) -> None:
    llm = MockStructuredLLM(valid_listing())
    agent = MarketplaceListingAgent(llm, adapter(), approval_engine())
    handlers = EventHandlerRegistry()
    agent.register(handlers)
    processor = EventProcessor(listing_session_factory, handlers)
    first_event, _ = publish_stock_event(listing_session_factory, aggregate_version=1)
    second_event, _ = publish_stock_event(listing_session_factory, aggregate_version=2)

    processor.process(first_event)
    processor.process(second_event)

    with listing_session_factory() as session:
        versions = list(
            session.scalars(
                select(ListingDraft.listing_version).order_by(ListingDraft.listing_version)
            )
        )
        assert versions == [1, 2]
        assert len(llm.requests) == 2
