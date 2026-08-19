import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce_operations.agents.listing_models import GeneratedListing
from commerce_operations.ai import LLMRequest, StructuredLLM, UsageAccounting
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalActionType, ApprovalContext
from commerce_operations.events.handlers import EventHandlerRegistry
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    EventEnvelope,
    EventPayload,
    EventType,
    ListingApprovedPayload,
    ListingDraftCreatedPayload,
    ListingValidationFailedPayload,
    StockReceivedPayload,
    create_event,
)
from commerce_operations.integrations.marketplaces import MarketplaceListingAdapter
from commerce_operations.persistence.enums import ApprovalStatus, RunStatus
from commerce_operations.persistence.models import (
    AgentRun,
    Approval,
    AuditEvent,
    InventoryItem,
    ListingDraft,
    ProcurementRequest,
    WorkflowRun,
)


class ListingInventoryNotReadyError(RuntimeError):
    pass


class ListingApprovalError(RuntimeError):
    pass


class MarketplaceListingAgent:
    def __init__(
        self,
        llm: StructuredLLM,
        adapter: MarketplaceListingAdapter,
        approval_engine: ApprovalEngine,
        *,
        prompt_version: str = "listing-v1",
        event_store: DatabaseEventStore | None = None,
        usage_accounting: UsageAccounting | None = None,
    ) -> None:
        self.llm = llm
        self.adapter = adapter
        self.approval_engine = approval_engine
        self.prompt_version = prompt_version
        self.event_store = event_store or DatabaseEventStore()
        self.usage_accounting = usage_accounting or UsageAccounting()

    @property
    def handler_name(self) -> str:
        return f"listing-agent:{self.adapter.marketplace}"

    def handle_stock_received(self, event: EventEnvelope[EventPayload], session: Session) -> None:
        payload = (
            event.data
            if isinstance(event.data, StockReceivedPayload)
            else StockReceivedPayload.model_validate(event.data.model_dump())
        )
        self.generate(
            session,
            inventory_item_id=payload.inventory_item_id,
            correlation_id=event.correlation_id,
            causation_id=event.event_id,
            source_event_id=event.event_id,
        )

    def register(self, registry: EventHandlerRegistry) -> None:
        registry.register(EventType.STOCK_RECEIVED, self.handler_name, self.handle_stock_received)

    def generate(
        self,
        session: Session,
        *,
        inventory_item_id: uuid.UUID,
        correlation_id: uuid.UUID,
        causation_id: uuid.UUID | None,
        source_event_id: uuid.UUID | None = None,
    ) -> ListingDraft:
        inventory = session.get(InventoryItem, inventory_item_id)
        if inventory is None or inventory.available_quantity <= 0:
            raise ListingInventoryNotReadyError("Inventory is not listing-ready")
        product = inventory.product
        listing_version = (
            session.scalar(
                select(func.max(ListingDraft.listing_version)).where(
                    ListingDraft.inventory_item_id == inventory.id,
                    ListingDraft.marketplace == self.adapter.marketplace,
                )
            )
            or 0
        ) + 1
        workflow_correlation = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"listing:{source_event_id or correlation_id}:{self.adapter.marketplace}",
        )
        workflow = WorkflowRun(
            workflow_name=f"marketplace-listing:{self.adapter.marketplace}",
            workflow_version=1,
            correlation_id=workflow_correlation,
            status=RunStatus.RUNNING,
            current_step="ai_generation",
            checkpoints={
                "inventory_item_id": str(inventory.id),
                "source_event_id": str(source_event_id) if source_event_id else None,
                "marketplace": self.adapter.marketplace,
            },
        )
        session.add(workflow)
        session.flush()
        agent_run = AgentRun(
            workflow_run_id=workflow.id,
            agent_type="marketplace_listing",
            status=RunStatus.RUNNING,
            input_reference=str(source_event_id or inventory.id),
            prompt_version=self.prompt_version,
        )
        session.add(agent_run)
        session.flush()
        self.usage_accounting.authorize(session, workflow)

        request = LLMRequest(
            task="generate_marketplace_listing",
            prompt_version=self.prompt_version,
            context={
                "product": {
                    "name": product.name,
                    "brand": product.brand,
                    "identifiers": product.identifiers,
                    "attributes": product.attributes,
                },
                "inventory": {
                    "sku": inventory.sku,
                    "available_quantity": inventory.available_quantity,
                    "cost_basis": str(inventory.cost_basis),
                    "currency": inventory.currency,
                },
                "commercial_guidance": {
                    "recommended_selling_price": str(self._recommended_price(session, inventory))
                },
            },
            constraints=self.adapter.generation_constraints(),
        )
        result = self.llm.generate(request, GeneratedListing)
        generated = GeneratedListing.model_validate(result.output)
        issues = self.adapter.validate(generated, expected_sku=inventory.sku)
        valid = not issues

        agent_run.status = RunStatus.SUCCEEDED
        agent_run.provider = result.provider
        agent_run.model = result.model
        self.usage_accounting.record(workflow, agent_run, result.usage)
        agent_run.safety_result = {
            "deterministic_validation_passed": valid,
            "validation_issue_count": len(issues),
        }
        draft = ListingDraft(
            inventory_item_id=inventory.id,
            workflow_run_id=workflow.id,
            agent_run_id=agent_run.id,
            marketplace=self.adapter.marketplace,
            listing_version=listing_version,
            title=generated.title,
            bullet_points=generated.bullet_points,
            description=generated.description,
            category=generated.category_suggestion,
            attributes=generated.product_attributes,
            search_keywords=generated.search_terms,
            sku=generated.sku,
            proposed_price=generated.proposed_price,
            currency=inventory.currency,
            image_requirements=[
                requirement.model_dump(mode="json") for requirement in generated.image_requirements
            ],
            marketplace_payload=self.adapter.build_payload(generated),
            validation_results={
                "valid": valid,
                "issues": [issue.model_dump(mode="json") for issue in issues],
            },
            approval_status=(ApprovalStatus.PENDING if valid else ApprovalStatus.REJECTED),
            provider_metadata=result.provider_metadata or {},
            structured_ai_response=generated.model_dump(mode="json"),
            prompt_version=self.prompt_version,
            ai_provider=result.provider,
            ai_model=result.model,
        )
        session.add(draft)
        session.flush()
        agent_run.output_reference = str(draft.id)
        self._audit_draft(session, draft, agent_run)
        self.event_store.publish(
            session,
            create_event(
                ListingDraftCreatedPayload(
                    listing_draft_id=draft.id,
                    inventory_item_id=inventory.id,
                    marketplace=self.adapter.marketplace,
                    listing_version=listing_version,
                ),
                aggregate_type="listing_draft",
                aggregate_id=draft.id,
                aggregate_version=draft.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                causation_id=causation_id,
                idempotency_key=f"listing-draft-created:{draft.id}",
            ),
        )
        if valid:
            approval = self.approval_engine.request_if_required(
                session,
                ApprovalContext(
                    ApprovalActionType.FIRST_MARKETPLACE_PUBLICATION,
                    is_first_publication=True,
                    risk_level="public_commercial_action",
                ),
                action_type=ApprovalActionType.FIRST_MARKETPLACE_PUBLICATION.value,
                resource_type="listing_draft",
                resource_id=draft.id,
                requested_action={
                    "operation": "approve_first_marketplace_listing",
                    "marketplace": draft.marketplace,
                    "listing_version": draft.listing_version,
                    "payload": draft.marketplace_payload,
                },
                reason="First marketplace publication requires human review",
                requester=self.handler_name,
                risk_level="public_commercial_action",
                workflow_run_id=workflow.id,
            )
            if approval is None:
                raise RuntimeError("First publication policy unexpectedly allowed publication")
            draft.approval_id = approval.id
            workflow.status = RunStatus.PENDING
            workflow.current_step = "awaiting_listing_approval"
        else:
            workflow.status = RunStatus.FAILED
            workflow.current_step = "validation_failed"
            workflow.error = {
                "type": "deterministic_listing_validation",
                "issues": [issue.model_dump(mode="json") for issue in issues],
            }
            self.event_store.publish(
                session,
                create_event(
                    ListingValidationFailedPayload(
                        listing_draft_id=draft.id,
                        inventory_item_id=inventory.id,
                        marketplace=self.adapter.marketplace,
                        error_count=len(issues),
                    ),
                    aggregate_type="listing_draft",
                    aggregate_id=draft.id,
                    aggregate_version=draft.version,
                    correlation_id=workflow.correlation_id,
                    workflow_id=workflow.id,
                    causation_id=causation_id,
                    idempotency_key=f"listing-validation-failed:{draft.id}",
                ),
            )
        session.flush()
        return draft

    @staticmethod
    def _recommended_price(session: Session, inventory: InventoryItem) -> Decimal:
        procurement = session.scalar(
            select(ProcurementRequest)
            .where(ProcurementRequest.product_id == inventory.product_id)
            .order_by(ProcurementRequest.created_at.desc())
        )
        if procurement is not None:
            value = procurement.recommendation_context.get("economics", {}).get(
                "recommended_selling_price"
            )
            if value is not None:
                return Decimal(str(value))
        return inventory.cost_basis * 2

    @staticmethod
    def _audit_draft(session: Session, draft: ListingDraft, agent_run: AgentRun) -> None:
        session.add(
            AuditEvent(
                actor_type="agent",
                actor_id="marketplace_listing",
                action="listing.draft_created",
                resource_type="listing_draft",
                resource_id=draft.id,
                after_state={
                    "marketplace": draft.marketplace,
                    "listing_version": draft.listing_version,
                    "valid": draft.validation_results["valid"],
                },
                reason="Structured AI listing generation",
                correlation_id=agent_run.workflow_run.correlation_id,
            )
        )


class ListingApprovalService:
    def __init__(self, event_store: DatabaseEventStore | None = None) -> None:
        self.event_store = event_store or DatabaseEventStore()

    def approve_listing(self, approval: Approval, session: Session) -> None:
        if approval.resource_type != "listing_draft":
            raise ListingApprovalError("Listing approval references the wrong entity type")
        draft = session.scalar(
            select(ListingDraft).where(ListingDraft.id == approval.resource_id).with_for_update()
        )
        if draft is None:
            raise ListingApprovalError("Listing draft was not found")
        if draft.approval_id != approval.id or draft.approval_status is not ApprovalStatus.PENDING:
            raise ListingApprovalError("Listing draft is not awaiting this approval")
        if not draft.validation_results.get("valid"):
            raise ListingApprovalError("Invalid listing draft cannot be approved")
        draft.approval_status = ApprovalStatus.APPROVED
        workflow = session.get(WorkflowRun, draft.workflow_run_id)
        if workflow is None:
            raise ListingApprovalError("Listing workflow was not found")
        workflow.status = RunStatus.SUCCEEDED
        workflow.current_step = "listing_approved"
        self.event_store.publish(
            session,
            create_event(
                ListingApprovedPayload(
                    listing_draft_id=draft.id,
                    approval_id=approval.id,
                ),
                aggregate_type="listing_draft",
                aggregate_id=draft.id,
                aggregate_version=draft.version,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"listing-approved:{draft.id}",
            ),
        )
        session.add(
            AuditEvent(
                actor_type="user",
                actor_id=approval.decided_by or "unknown",
                action="listing.approved",
                resource_type="listing_draft",
                resource_id=draft.id,
                before_state={"approval_status": "pending"},
                after_state={"approval_status": "approved"},
                reason=approval.rationale,
                correlation_id=workflow.correlation_id,
            )
        )


def register_listing_approval_handler(registry: ApprovalActionRegistry) -> None:
    service = ListingApprovalService()
    registry.register(
        ApprovalActionType.FIRST_MARKETPLACE_PUBLICATION.value,
        service.approve_listing,
    )
