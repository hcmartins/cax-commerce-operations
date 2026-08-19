import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.events.handlers import EventHandlerRegistry
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    EventEnvelope,
    EventPayload,
    EventType,
    ListingApprovedPayload,
    ListingPublishedPayload,
    create_event,
)
from commerce_operations.integrations.marketplaces import (
    MarketplaceConnectorRegistry,
    MarketplaceListingInput,
    MarketplaceValidationError,
)
from commerce_operations.persistence.enums import ApprovalStatus, PublicationStatus, RunStatus
from commerce_operations.persistence.models import (
    AuditEvent,
    InventoryItem,
    ListingDraft,
    MarketplaceListing,
    WorkflowRun,
)


class ListingPublicationError(RuntimeError):
    pass


class MarketplacePublicationService:
    def __init__(
        self,
        connectors: MarketplaceConnectorRegistry,
        event_store: DatabaseEventStore | None = None,
    ) -> None:
        self.connectors = connectors
        self.event_store = event_store or DatabaseEventStore()

    def register(self, handlers: EventHandlerRegistry) -> None:
        handlers.register(
            EventType.LISTING_APPROVED,
            "marketplace-publication",
            self.handle_listing_approved,
        )

    def handle_listing_approved(self, event: EventEnvelope[EventPayload], session: Session) -> None:
        payload = ListingApprovedPayload.model_validate(event.data)
        draft = session.scalar(
            select(ListingDraft)
            .where(ListingDraft.id == payload.listing_draft_id)
            .with_for_update()
        )
        if draft is None:
            raise ListingPublicationError("Approved listing draft was not found")
        if draft.approval_status is not ApprovalStatus.APPROVED:
            raise ListingPublicationError("Only approved listing drafts may be published")
        connector = self.connectors.get(draft.marketplace)
        existing = session.scalar(
            select(MarketplaceListing).where(MarketplaceListing.draft_id == draft.id)
        )
        if existing is not None and existing.publication_status is PublicationStatus.PUBLISHED:
            return
        listing = existing or MarketplaceListing(
            draft_id=draft.id,
            marketplace=draft.marketplace,
            marketplace_account_id=connector.account_id,
            sku=draft.sku,
            publication_status=PublicationStatus.PENDING,
        )
        if existing is None:
            session.add(listing)
            session.flush()

        publication_input = self._input(draft)
        validation = connector.validate_listing(publication_input)
        if not validation.valid:
            listing.publication_status = PublicationStatus.FAILED
            listing.last_error = "; ".join(validation.issues)
            self._audit(session, draft, listing, event.correlation_id, "validation_failed")
            return
        try:
            result = connector.create_listing(
                publication_input,
                idempotency_key=f"listing-draft:{draft.id}",
            )
        except MarketplaceValidationError as exc:
            listing.publication_status = PublicationStatus.FAILED
            listing.last_error = "; ".join(exc.issues)
            self._audit(session, draft, listing, event.correlation_id, "validation_failed")
            return

        listing.external_listing_id = result.external_listing_id
        listing.publication_status = PublicationStatus.PUBLISHED
        listing.published_at = datetime.now(UTC)
        listing.synchronised_at = listing.published_at
        listing.last_error = None
        workflow = (
            session.get(WorkflowRun, draft.workflow_run_id) if draft.workflow_run_id else None
        )
        if workflow is not None:
            workflow.status = RunStatus.SUCCEEDED
            workflow.current_step = "listing_published"
        self.event_store.publish(
            session,
            create_event(
                ListingPublishedPayload(
                    marketplace_listing_id=listing.id,
                    listing_draft_id=draft.id,
                    external_listing_id=result.external_listing_id,
                ),
                aggregate_type="marketplace_listing",
                aggregate_id=listing.id,
                aggregate_version=listing.version,
                correlation_id=event.correlation_id,
                workflow_id=draft.workflow_run_id,
                causation_id=event.event_id,
                idempotency_key=f"listing-published:{draft.id}",
            ),
        )
        self._audit(session, draft, listing, event.correlation_id, "published")

    @staticmethod
    def _input(draft: ListingDraft) -> MarketplaceListingInput:
        inventory: InventoryItem = draft.inventory_item
        payload = draft.marketplace_payload
        return MarketplaceListingInput(
            sku=draft.sku,
            title=draft.title,
            description=draft.description,
            category=draft.category,
            attributes=draft.attributes,
            price=draft.proposed_price,
            currency=draft.currency,
            quantity=inventory.available_quantity,
            image_urls=tuple(payload.get("image_urls", ())),
            marketplace_payload=payload,
        )

    @staticmethod
    def _audit(
        session: Session,
        draft: ListingDraft,
        listing: MarketplaceListing,
        correlation_id: uuid.UUID,
        outcome: str,
    ) -> None:
        session.add(
            AuditEvent(
                actor_type="system",
                actor_id=f"marketplace:{draft.marketplace}",
                action="listing.publication",
                resource_type="marketplace_listing",
                resource_id=listing.id,
                after_state={
                    "outcome": outcome,
                    "external_listing_id": listing.external_listing_id,
                },
                reason="Approved marketplace listing publication",
                correlation_id=correlation_id,
            )
        )
