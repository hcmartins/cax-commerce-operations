import uuid
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.application.marketplace_publication import MarketplacePublicationService
from commerce_operations.events import (
    DatabaseEventStore,
    EventHandlerRegistry,
    EventProcessor,
    create_event,
)
from commerce_operations.events.types import ListingApprovedPayload
from commerce_operations.integrations.marketplaces import (
    MarketplaceConnectorRegistry,
    MarketplaceListingResult,
    MarketplaceValidationResult,
)
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import ApprovalStatus, PublicationStatus
from commerce_operations.persistence.models import (
    DomainEvent,
    InventoryItem,
    ListingDraft,
    MarketplaceListing,
    Product,
)


class MockConnector:
    marketplace = "ebay"
    account_id = "sandbox-seller"

    def __init__(self):
        self.created = []

    def validate_listing(self, listing):
        return MarketplaceValidationResult(True)

    def create_listing(self, listing, *, idempotency_key):
        self.created.append((listing, idempotency_key))
        return MarketplaceListingResult("sandbox-listing-123", "published")


def test_approved_listing_is_published_once_and_emits_event(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'publication.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id="p1",
            source_workflow_run_id="workflow-1",
            source_recommendation_id="recommendation-1",
            source_payload_hash="hash",
            name="Bottle",
        )
        session.add(product)
        session.flush()
        inventory = InventoryItem(
            product_id=product.id,
            sku="BOTTLE-1",
            storage_location="warehouse",
            quantity_on_hand=10,
            reserved_quantity=2,
            cost_basis=Decimal("5"),
            currency="GBP",
            low_stock_threshold=2,
        )
        session.add(inventory)
        session.flush()
        draft = ListingDraft(
            inventory_item_id=inventory.id,
            marketplace="ebay",
            listing_version=1,
            title="Bottle",
            bullet_points=[],
            description="Insulated bottle",
            category="123",
            attributes={"material": "steel"},
            search_keywords=[],
            sku="BOTTLE-1",
            proposed_price=Decimal("14.99"),
            currency="GBP",
            image_requirements={},
            marketplace_payload={
                "condition": "NEW",
                "image_urls": ["https://cdn.example/bottle.jpg"],
            },
            validation_results={"valid": True},
            approval_status=ApprovalStatus.APPROVED,
        )
        session.add(draft)
        session.flush()
        event = DatabaseEventStore().publish(
            session,
            create_event(
                ListingApprovedPayload(listing_draft_id=draft.id, approval_id=uuid.uuid4()),
                aggregate_type="listing_draft",
                aggregate_id=draft.id,
                aggregate_version=draft.version,
                idempotency_key=f"listing-approved:{draft.id}",
            ),
        )
        event_id = event.id
        draft_id = draft.id

    connector = MockConnector()
    connectors = MarketplaceConnectorRegistry()
    connectors.register(connector)
    handlers = EventHandlerRegistry()
    MarketplacePublicationService(connectors).register(handlers)
    processor = EventProcessor(factory, handlers)

    assert processor.process(event_id).handlers_processed == 1
    assert processor.process(event_id).handlers_skipped == 1
    assert len(connector.created) == 1
    assert connector.created[0][0].quantity == 8
    assert connector.created[0][1] == f"listing-draft:{draft_id}"

    with factory() as session:
        listing = session.scalar(select(MarketplaceListing))
        assert listing is not None
        assert listing.external_listing_id == "sandbox-listing-123"
        assert listing.publication_status is PublicationStatus.PUBLISHED
        assert session.scalar(select(func.count()).select_from(MarketplaceListing)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "LISTING_PUBLISHED")
            )
            == 1
        )
    engine.dispose()
