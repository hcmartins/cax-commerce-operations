import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import ProductApprovedPayload, create_event
from commerce_operations.integrations.repository_1.contracts import (
    ApprovedProductRequestV1,
    ApprovedProductResponseV1,
)
from commerce_operations.persistence.enums import (
    ProcurementStatus,
    ProductStatus,
    RunStatus,
    SupplierStatus,
)
from commerce_operations.persistence.models import (
    DomainEvent,
    ProcurementRequest,
    Product,
    Supplier,
    SupplierQuote,
    WorkflowRun,
)


class ApprovedProductConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestionIdentity:
    source_system: str
    source_product_id: str


class ApprovedProductIngestionService:
    WORKFLOW_NAME = "approved-product-procurement"

    def __init__(self, event_store: DatabaseEventStore | None = None) -> None:
        self.event_store = event_store or DatabaseEventStore()

    def ingest(
        self,
        session: Session,
        request: ApprovedProductRequestV1,
        *,
        request_idempotency_key: str,
    ) -> ApprovedProductResponseV1:
        identity = IngestionIdentity(request.source_system, request.source_product_id)
        payload_hash = self._payload_hash(request)
        existing = self._find_product(session, identity)
        if existing is not None:
            return self._existing_response(session, existing, payload_hash)

        product = Product(
            source_system=request.source_system,
            external_product_id=request.source_product_id,
            source_workflow_run_id=request.source_workflow_run_id,
            source_recommendation_id=request.source_recommendation_id,
            source_payload_hash=payload_hash,
            name=request.product.name,
            brand=request.product.brand,
            identifiers=request.product.identifiers,
            attributes=request.product.attributes,
            recommendation_evidence=request.recommendation.model_dump(mode="json")["evidence"],
            status=ProductStatus.APPROVED,
        )
        session.add(product)

        supplier = session.scalar(
            select(Supplier).where(
                Supplier.source_system == request.source_system,
                Supplier.external_supplier_id == request.selected_supplier.source_supplier_id,
            )
        )
        if supplier is None:
            supplier = Supplier(
                source_system=request.source_system,
                external_supplier_id=request.selected_supplier.source_supplier_id,
                name=request.selected_supplier.name,
                contact_details=request.selected_supplier.contact_details,
                terms=request.selected_supplier.terms,
                status=SupplierStatus.ACTIVE,
            )
            session.add(supplier)

        session.flush()
        quote = SupplierQuote(
            product_id=product.id,
            supplier_id=supplier.id,
            external_quote_id=request.supplier_quote.source_quote_id,
            currency=request.supplier_quote.currency,
            moq=request.supplier_quote.moq,
            quantity=request.supplier_quote.quantity,
            unit_cost=request.supplier_quote.unit_cost,
            shipping_cost=request.supplier_quote.shipping_cost,
            lead_time_days=request.supplier_quote.lead_time_days,
            valid_until=request.supplier_quote.valid_until,
            source_reference=request.supplier_quote.source_reference,
        )
        session.add(quote)

        correlation_id = self._correlation_id(identity, request.source_workflow_run_id)
        workflow = WorkflowRun(
            workflow_name=self.WORKFLOW_NAME,
            workflow_version=1,
            correlation_id=correlation_id,
            status=RunStatus.PENDING,
            current_step="procurement_requested",
            checkpoints={
                "source_system": request.source_system,
                "source_workflow_run_id": request.source_workflow_run_id,
                "source_recommendation_id": request.source_recommendation_id,
                "request_idempotency_key": request_idempotency_key,
            },
        )
        session.add(workflow)
        session.flush()

        procurement = ProcurementRequest(
            product_id=product.id,
            selected_quote_id=quote.id,
            workflow_run_id=workflow.id,
            requested_quantity=request.supplier_quote.quantity,
            estimated_landed_cost=request.economics.estimated_landed_cost_per_unit,
            currency=request.supplier_quote.currency,
            recommendation_context={
                "economics": request.economics.model_dump(mode="json"),
                "recommendation": request.recommendation.model_dump(mode="json"),
            },
            expected_arrival_at=datetime.now(UTC)
            + timedelta(days=request.supplier_quote.lead_time_days),
            status=ProcurementStatus.PROPOSED,
        )
        session.add(procurement)
        session.flush()

        event = create_event(
            ProductApprovedPayload(
                product_id=product.id,
                source_product_id=request.source_product_id,
                source_workflow_run_id=request.source_workflow_run_id,
                procurement_request_id=procurement.id,
            ),
            aggregate_type="product",
            aggregate_id=product.id,
            aggregate_version=product.version,
            correlation_id=correlation_id,
            workflow_id=workflow.id,
            idempotency_key=self._event_idempotency_key(identity),
        )
        event_record = self.event_store.publish(session, event)
        return self._response(product, procurement, workflow, event_record, duplicate=False)

    def find_existing_after_conflict(
        self, session: Session, request: ApprovedProductRequestV1
    ) -> ApprovedProductResponseV1:
        identity = IngestionIdentity(request.source_system, request.source_product_id)
        product = self._find_product(session, identity)
        if product is None:
            raise ApprovedProductConflictError(
                "Concurrent ingestion failed without a saved product"
            )
        return self._existing_response(session, product, self._payload_hash(request))

    def _existing_response(
        self, session: Session, product: Product, payload_hash: str
    ) -> ApprovedProductResponseV1:
        if product.source_payload_hash != payload_hash:
            raise ApprovedProductConflictError(
                "The source product was already ingested with different approved data"
            )
        procurement = session.scalar(
            select(ProcurementRequest).where(ProcurementRequest.product_id == product.id)
        )
        event = session.scalar(
            select(DomainEvent).where(
                DomainEvent.idempotency_key
                == self._event_idempotency_key(
                    IngestionIdentity(product.source_system, product.external_product_id)
                )
            )
        )
        if procurement is None or event is None:
            raise ApprovedProductConflictError("Existing ingestion is incomplete")
        workflow = session.get(WorkflowRun, procurement.workflow_run_id)
        if workflow is None:
            raise ApprovedProductConflictError("Existing procurement workflow is missing")
        return self._response(product, procurement, workflow, event, duplicate=True)

    @staticmethod
    def _find_product(session: Session, identity: IngestionIdentity) -> Product | None:
        return session.scalar(
            select(Product).where(
                Product.source_system == identity.source_system,
                Product.external_product_id == identity.source_product_id,
            )
        )

    @staticmethod
    def _payload_hash(request: ApprovedProductRequestV1) -> str:
        canonical = json.dumps(
            request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _correlation_id(identity: IngestionIdentity, source_workflow_run_id: str) -> uuid.UUID:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{identity.source_system}:{identity.source_product_id}:{source_workflow_run_id}",
        )

    @staticmethod
    def _event_idempotency_key(identity: IngestionIdentity) -> str:
        return f"product-approved:{identity.source_system}:{identity.source_product_id}"

    @staticmethod
    def _response(
        product: Product,
        procurement: ProcurementRequest,
        workflow: WorkflowRun,
        event: DomainEvent,
        *,
        duplicate: bool,
    ) -> ApprovedProductResponseV1:
        return ApprovedProductResponseV1(
            product_id=product.id,
            procurement_request_id=procurement.id,
            workflow_run_id=workflow.id,
            event_id=event.id,
            correlation_id=workflow.correlation_id,
            procurement_status=procurement.status.value,
            duplicate=duplicate,
        )
