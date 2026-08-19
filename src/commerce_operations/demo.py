"""Safe, idempotent synthetic data for client demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from commerce_operations.config import Settings
from commerce_operations.persistence.database import create_database_engine, create_session_factory
from commerce_operations.persistence.enums import (
    ApprovalStatus,
    ConversationStatus,
    CustomerIntent,
    CustomerServiceDecision,
    EventStatus,
    InventoryMovementType,
    MessageDirection,
    MessageStatus,
    OrderStatus,
    ProcurementStatus,
    ProductStatus,
    PublicationStatus,
    PurchaseOrderStatus,
    RunStatus,
    SupplierStatus,
)
from commerce_operations.persistence.models import (
    AgentRun,
    Approval,
    AuditEvent,
    CustomerConversation,
    CustomerMessage,
    DomainEvent,
    InventoryItem,
    InventoryMovement,
    ListingDraft,
    MarketplaceListing,
    Order,
    OrderItem,
    PricingDecision,
    ProcurementRequest,
    Product,
    PurchaseOrder,
    Supplier,
    SupplierQuote,
    WorkflowRun,
)

DEMO_SOURCE = "commerce-demo-v1"
DEMO_ACCOUNT = "demo-sandbox-account"
NAMESPACE = uuid.UUID("0be93e2d-30d8-45e3-bdc2-3ecb0ec74a34")


def _id(value: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, value)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


PRODUCTS = (
    ("boot-organiser", "Premium Car Boot Organiser", "Car accessories", 50, 8, "8.20", "24.99"),
    (
        "drawer-dividers",
        "Adjustable Bamboo Drawer Dividers",
        "Home organisation",
        0,
        5,
        "6.10",
        "19.99",
    ),
    (
        "silicone-utensils",
        "Silicone Kitchen Utensil Set",
        "Kitchen accessories",
        0,
        6,
        "9.40",
        "27.99",
    ),
    ("pet-seat-cover", "Waterproof Pet Car Seat Cover", "Pet accessories", 36, 6, "11.60", "32.99"),
    ("desk-organiser", "Modular Desk Organiser", "Office accessories", 42, 7, "5.30", "17.99"),
    ("vacuum-bags", "Reusable Vacuum Storage Bags", "Storage products", 64, 10, "7.25", "22.99"),
    ("cable-clips", "Magnetic Cable Management Clips", "Office accessories", 4, 6, "2.80", "11.99"),
    ("pantry-bins", "Clear Stackable Pantry Bins", "Home organisation", 24, 5, "8.75", "26.99"),
    ("travel-bowls", "Collapsible Pet Travel Bowls", "Pet accessories", 18, 4, "3.10", "12.99"),
)


def _guard(settings: Settings) -> None:
    if not settings.demo_mode:
        raise RuntimeError("Demo data commands require COMMERCE_DEMO_MODE=true")
    if settings.is_production:
        raise RuntimeError("Demo data commands are disabled in production")


def seed_demo_data(session: Session, settings: Settings) -> int:
    """Seed synthetic lifecycle records once and return the number of products created."""
    _guard(settings)
    existing = session.scalar(
        select(Product.id).where(Product.source_system == DEMO_SOURCE).limit(1)
    )
    if existing:
        return 0

    now = datetime.now(UTC).replace(microsecond=0)
    supplier = Supplier(
        id=_id("supplier"),
        source_system=DEMO_SOURCE,
        external_supplier_id="DEMO-SUP-001",
        name="Northstar Demo Supplies",
        status=SupplierStatus.ACTIVE,
        contact_details={"note": "Synthetic demo supplier — no external ordering"},
        terms={"payment": "DEMO ONLY", "incoterm": "DAP"},
        created_at=now - timedelta(days=50),
    )
    session.add(supplier)

    for index, (key, name, category, stock, threshold, cost, price) in enumerate(PRODUCTS):
        created = now - timedelta(days=35 - index * 3)
        product = Product(
            id=_id(f"product:{key}"),
            source_system=DEMO_SOURCE,
            external_product_id=f"DEMO-{index + 1:03d}",
            source_workflow_run_id=f"demo-intel-{index + 1}",
            source_recommendation_id=f"demo-rec-{index + 1}",
            source_payload_hash=_hash(key),
            name=name,
            brand="Demo Essentials",
            identifiers={"demo": True},
            attributes={"category": category, "synthetic": True},
            recommendation_evidence=[{"source": "synthetic demo", "confidence": 0.91}],
            status=ProductStatus.APPROVED,
            created_at=created,
        )
        quote = SupplierQuote(
            id=_id(f"quote:{key}"),
            product=product,
            supplier=supplier,
            external_quote_id=f"DEMO-Q-{index + 1:03d}",
            currency="GBP",
            moq=20,
            quantity=50,
            unit_cost=Decimal(cost) - Decimal("1.00"),
            shipping_cost=Decimal("50.00"),
            lead_time_days=14,
            source_reference="synthetic-demo-quote",
            created_at=created + timedelta(hours=2),
        )
        workflow_status = RunStatus.SUCCEEDED
        current_step = "completed"
        error = None
        if key == "drawer-dividers":
            workflow_status, current_step = RunStatus.RUNNING, "awaiting_purchase_approval"
        elif key == "pantry-bins":
            workflow_status, current_step = RunStatus.FAILED, "generate_listing"
            error = {
                "code": "DEMO_TRANSIENT_AI_FAILURE",
                "message": "Synthetic retryable provider timeout",
            }
        workflow = WorkflowRun(
            id=_id(f"workflow:{key}"),
            workflow_name="product_lifecycle",
            workflow_version=1,
            correlation_id=_id(f"correlation:{key}"),
            idempotency_key=f"demo-workflow:{key}",
            status=workflow_status,
            current_step=current_step,
            attempts=2 if error else 1,
            max_attempts=3,
            timeout_seconds=300,
            error=error,
            checkpoints={"demo": True},
            started_at=created,
            completed_at=created + timedelta(days=12)
            if workflow_status == RunStatus.SUCCEEDED
            else None,
            next_retry_at=now + timedelta(minutes=10) if error else None,
            cost_amount=Decimal("0.08"),
            cost_currency="GBP",
            created_at=created,
        )
        request_status = ProcurementStatus.RECEIVED if stock else ProcurementStatus.PROPOSED
        if key == "drawer-dividers":
            request_status = ProcurementStatus.AWAITING_APPROVAL
        elif key == "silicone-utensils":
            request_status = ProcurementStatus.SHIPPED
        request = ProcurementRequest(
            id=_id(f"procurement:{key}"),
            product=product,
            selected_quote=quote,
            workflow_run=workflow,
            requested_quantity=50,
            estimated_landed_cost=Decimal(cost) * 50,
            currency="GBP",
            recommendation_context={
                "predicted_roi": "41.20",
                "predicted_margin": "32.00",
                "demo": True,
            },
            expected_arrival_at=now + timedelta(days=5),
            status=request_status,
            created_at=created + timedelta(hours=4),
        )
        session.add_all([product, quote, workflow, request])

        session.add(
            DomainEvent(
                id=_id(f"event:approved:{key}"),
                event_type="PRODUCT_APPROVED",
                event_version=1,
                producer="demo-seeder",
                aggregate_type="product",
                aggregate_id=product.id,
                aggregate_version=1,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                idempotency_key=f"demo:event:approved:{key}",
                payload={"product_id": str(product.id), "product_name": name, "demo": True},
                publication_status=EventStatus.PUBLISHED,
                published_at=created,
                created_at=created,
            )
        )

        if key == "drawer-dividers":
            approval = Approval(
                id=_id(f"approval:{key}"),
                action_type="supplier_purchase",
                resource_type="procurement_request",
                resource_id=request.id,
                requested_payload={"quantity": 50, "currency": "GBP", "demo": True},
                action_hash=_hash(f"approval:{key}"),
                risk_level="medium",
                rule_name="supplier_purchase",
                rule_version=1,
                status=ApprovalStatus.PENDING,
                requested_by="procurement-service",
                requested_reason="Approve synthetic demo purchase commitment",
                workflow_run=workflow,
                expires_at=now + timedelta(days=3),
                created_at=created + timedelta(hours=5),
            )
            session.add(approval)
            workflow.waiting_approval_id = approval.id

        if request_status in {ProcurementStatus.SHIPPED, ProcurementStatus.RECEIVED}:
            po_status = PurchaseOrderStatus.SHIPPED if not stock else PurchaseOrderStatus.RECEIVED
            po = PurchaseOrder(
                id=_id(f"po:{key}"),
                procurement_request=request,
                supplier=supplier,
                po_number=f"DEMO-PO-{index + 1:03d}",
                quantity=50,
                received_quantity=50 if stock else 0,
                total_amount=Decimal(cost) * 50,
                actual_landed_cost=Decimal(cost) * 50 if stock else None,
                currency="GBP",
                terms={"demo": True, "payment": "NO PAYMENT"},
                external_reference="DEMO-NO-SUPPLIER-ORDER",
                status=po_status,
                ordered_at=created + timedelta(days=1),
                shipped_at=created + timedelta(days=3),
                received_at=created + timedelta(days=8) if stock else None,
                expected_arrival_at=now + timedelta(days=5),
                created_at=created + timedelta(days=1),
            )
            session.add(po)
        else:
            po = None

        if not stock:
            continue
        item = InventoryItem(
            id=_id(f"inventory:{key}"),
            product=product,
            sku=f"DEMO-{key.upper()[:12]}",
            storage_location="Demo warehouse · A1",
            quantity_on_hand=stock,
            reserved_quantity=0,
            cost_basis=Decimal(cost),
            currency="GBP",
            low_stock_threshold=threshold,
            created_at=created + timedelta(days=8),
        )
        movement = InventoryMovement(
            id=_id(f"movement:{key}"),
            inventory_item=item,
            purchase_order=po,
            movement_type=InventoryMovementType.RECEIPT,
            quantity_delta=stock,
            unit_cost=Decimal(cost),
            reason="Synthetic demo goods receipt",
            source_type="demo_purchase_order",
            source_id=str(po.id),
            idempotency_key=f"demo:receipt:{key}",
            created_at=created + timedelta(days=8),
        )
        decision = PricingDecision(
            id=_id(f"pricing:{key}"),
            inventory_item=item,
            landed_cost=Decimal(cost),
            marketplace_fees=Decimal("2.50"),
            fulfilment_costs=Decimal("1.20"),
            target_margin=Decimal("0.32"),
            minimum_margin=Decimal("0.15"),
            minimum_price=Decimal(cost) + Decimal("5.00"),
            current_price=Decimal(price),
            proposed_price=Decimal(price),
            contribution_margin=Decimal(price) - Decimal(cost) - Decimal("3.70"),
            gross_profit=Decimal(price) - Decimal(cost),
            contribution_profit=Decimal(price) - Decimal(cost) - Decimal("3.70"),
            margin_percentage=Decimal("32.00"),
            roi_percentage=Decimal("41.20"),
            recommended_price=Decimal(price),
            price_change_percentage=Decimal("0"),
            landed_cost_source="actual",
            commercial_rules={"demo": True},
            currency="GBP",
            reason="Synthetic deterministic demo pricing",
            policy_result="safe",
            formula_version=1,
            effective_at=created + timedelta(days=8),
            created_at=created + timedelta(days=8),
        )
        session.add_all([item, movement, decision])
        session.add(
            DomainEvent(
                id=_id(f"event:stock:{key}"),
                event_type="STOCK_RECEIVED",
                event_version=1,
                producer="inventory-service",
                aggregate_type="inventory_item",
                aggregate_id=item.id,
                aggregate_version=1,
                correlation_id=workflow.correlation_id,
                workflow_id=workflow.id,
                causation_id=_id(f"event:approved:{key}"),
                idempotency_key=f"demo:event:stock:{key}",
                payload={
                    "product_id": str(product.id),
                    "product_name": name,
                    "quantity": stock,
                    "demo": True,
                },
                publication_status=EventStatus.PUBLISHED,
                published_at=created + timedelta(days=8),
                created_at=created + timedelta(days=8),
            )
        )

        if key == "pet-seat-cover":
            continue  # received and ready for the listing agent
        agent = AgentRun(
            id=_id(f"agent:{key}"),
            workflow_run=workflow,
            agent_type="marketplace_listing",
            status=RunStatus.FAILED if key == "pantry-bins" else RunStatus.SUCCEEDED,
            input_reference=str(item.id),
            output_reference=f"demo-listing:{key}",
            provider="demo-structured-ai",
            model="synthetic-no-api",
            prompt_version="listing-v1",
            input_tokens=620,
            output_tokens=280,
            cost_amount=Decimal("0.03"),
            cost_currency="GBP",
            safety_result={"safe": True, "demo": True},
            error=error,
            created_at=created + timedelta(days=9),
        )
        if key == "pantry-bins":
            session.add(agent)
            continue
        listing_approval = Approval(
            id=_id(f"listing-approval:{key}"),
            action_type="first_marketplace_publication",
            resource_type="listing_draft",
            resource_id=_id(f"draft:{key}"),
            requested_payload={"marketplace": "ebay", "demo": True},
            action_hash=_hash(f"listing:{key}"),
            risk_level="medium",
            rule_name="first_publication",
            rule_version=1,
            status=ApprovalStatus.PENDING if key == "desk-organiser" else ApprovalStatus.APPROVED,
            requested_by="listing-agent",
            requested_reason="Review synthetic AI-generated listing",
            workflow_run=workflow,
            expires_at=now + timedelta(days=3),
            decided_by=None if key == "desk-organiser" else "demo.operator",
            decided_at=None if key == "desk-organiser" else created + timedelta(days=10),
            created_at=created + timedelta(days=9),
        )
        draft = ListingDraft(
            id=_id(f"draft:{key}"),
            inventory_item=item,
            workflow_run=workflow,
            agent_run=agent,
            approval=listing_approval,
            marketplace="ebay",
            listing_version=1,
            title=f"{name} | Practical everyday essential",
            bullet_points=["Durable design", "Easy to use", "Demo listing — not for sale"],
            description=f"Synthetic sandbox listing for {name}. No real transaction can occur.",
            category=category,
            attributes={"condition": "new", "demo": True},
            search_keywords=[key, category.lower()],
            sku=item.sku,
            proposed_price=Decimal(price),
            currency="GBP",
            image_requirements={"minimum_images": 3},
            marketplace_payload={"sandbox": True},
            validation_results={"valid": True},
            approval_status=listing_approval.status,
            provider_metadata={"demo": True},
            structured_ai_response={"title": name, "synthetic": True},
            prompt_version="listing-v1",
            ai_provider="demo-structured-ai",
            ai_model="synthetic-no-api",
            created_at=created + timedelta(days=9),
        )
        session.add_all([agent, listing_approval, draft])
        if key == "desk-organiser":
            continue
        listing = MarketplaceListing(
            id=_id(f"listing:{key}"),
            draft=draft,
            marketplace="ebay",
            marketplace_account_id=DEMO_ACCOUNT,
            sku=item.sku,
            external_listing_id=f"DEMO-SANDBOX-{index + 1:03d}",
            publication_status=PublicationStatus.PUBLISHED,
            published_at=created + timedelta(days=10),
            synchronised_at=created + timedelta(days=10),
            created_at=created + timedelta(days=10),
        )
        decision.listing = listing
        session.add(listing)

        sold = 8 if key == "boot-organiser" else (3 if key == "cable-clips" else 5)
        item.quantity_on_hand = stock - sold
        for order_no in range(1, 3):
            qty = sold // 2 if order_no == 1 else sold - sold // 2
            order = Order(
                id=_id(f"order:{key}:{order_no}"),
                marketplace="ebay",
                marketplace_account_id=DEMO_ACCOUNT,
                external_order_id=f"DEMO-ORDER-{index + 1:03d}-{order_no}",
                source_event_id=f"demo-webhook-{key}-{order_no}",
                source_payload_hash=_hash(f"order:{key}:{order_no}"),
                status=OrderStatus.DELIVERED,
                currency="GBP",
                total_amount=Decimal(price) * qty,
                customer_reference="synthetic-customer",
                shipping_details={"redacted": True, "demo": True},
                ordered_at=created + timedelta(days=11, hours=order_no),
                paid_at=created + timedelta(days=11, hours=order_no),
                dispatched_at=created + timedelta(days=12),
                delivered_at=created + timedelta(days=14),
                created_at=created + timedelta(days=11, hours=order_no),
            )
            line = OrderItem(
                id=_id(f"order-item:{key}:{order_no}"),
                order=order,
                inventory_item=item,
                external_line_id=f"line-{order_no}",
                sku=item.sku,
                quantity=qty,
                unit_price=Decimal(price),
                tax_amount=Decimal("0"),
                discount_amount=Decimal("0"),
                reservation_reference=f"demo-reservation-{order_no}",
                created_at=order.ordered_at,
            )
            session.add_all([order, line])

        if key == "boot-organiser":
            conversation = CustomerConversation(
                id=_id("conversation:boot"),
                order=order,
                product=product,
                marketplace="ebay",
                marketplace_account_id=DEMO_ACCOUNT,
                external_conversation_id="DEMO-CONV-001",
                customer_reference="synthetic-customer",
                classification="order_status",
                risk_level="low",
                status=ConversationStatus.CLOSED,
                created_at=created + timedelta(days=13),
            )
            message = CustomerMessage(
                id=_id("message:boot"),
                conversation=conversation,
                external_message_id="DEMO-MSG-001",
                direction=MessageDirection.INBOUND,
                channel="marketplace",
                content="When will my demo order arrive?",
                author_type="synthetic_customer",
                status=MessageStatus.APPROVED,
                intent=CustomerIntent.ORDER_STATUS,
                classification=CustomerServiceDecision.AUTO_RESPOND,
                risk_level="low",
                generated_response="Your synthetic order has been dispatched.",
                final_response="Your synthetic order has been dispatched.",
                prompt_version="support-v1",
                ai_provider="demo-structured-ai",
                ai_model="synthetic-no-api",
                input_tokens=120,
                output_tokens=35,
                ai_cost_amount=Decimal("0.01"),
                ai_cost_currency="GBP",
                responded_at=created + timedelta(days=13, minutes=1),
                created_at=created + timedelta(days=13),
            )
            session.add_all([conversation, message])

    session.flush()
    return len(PRODUCTS)


def remove_demo_data(session: Session, settings: Settings) -> int:
    """Remove only records connected to the synthetic demo product IDs."""
    _guard(settings)
    products = session.scalars(select(Product).where(Product.source_system == DEMO_SOURCE)).all()
    if not products:
        return 0
    product_ids = [p.id for p in products]
    inventory_ids = list(
        session.scalars(select(InventoryItem.id).where(InventoryItem.product_id.in_(product_ids)))
    )
    request_ids = list(
        session.scalars(
            select(ProcurementRequest.id).where(ProcurementRequest.product_id.in_(product_ids))
        )
    )
    workflow_ids = list(
        session.scalars(
            select(ProcurementRequest.workflow_run_id).where(
                ProcurementRequest.product_id.in_(product_ids)
            )
        )
    )
    order_ids = (
        list(
            session.scalars(
                select(OrderItem.order_id).where(OrderItem.inventory_item_id.in_(inventory_ids))
            )
        )
        if inventory_ids
        else []
    )
    draft_ids = (
        list(
            session.scalars(
                select(ListingDraft.id).where(ListingDraft.inventory_item_id.in_(inventory_ids))
            )
        )
        if inventory_ids
        else []
    )
    approval_ids = (
        list(
            session.scalars(select(ListingDraft.approval_id).where(ListingDraft.id.in_(draft_ids)))
        )
        if draft_ids
        else []
    )
    conversation_ids = list(
        session.scalars(
            select(CustomerConversation.id).where(CustomerConversation.product_id.in_(product_ids))
        )
    )
    for model, condition in (
        (CustomerMessage, CustomerMessage.conversation_id.in_(conversation_ids)),
        (CustomerConversation, CustomerConversation.id.in_(conversation_ids)),
        (OrderItem, OrderItem.order_id.in_(order_ids)),
        (Order, Order.id.in_(order_ids)),
        (PricingDecision, PricingDecision.inventory_item_id.in_(inventory_ids)),
        (MarketplaceListing, MarketplaceListing.draft_id.in_(draft_ids)),
        (ListingDraft, ListingDraft.id.in_(draft_ids)),
        (Approval, Approval.id.in_(approval_ids)),
        (InventoryMovement, InventoryMovement.inventory_item_id.in_(inventory_ids)),
        (InventoryItem, InventoryItem.id.in_(inventory_ids)),
        (PurchaseOrder, PurchaseOrder.procurement_request_id.in_(request_ids)),
        (Approval, Approval.resource_id.in_(request_ids)),
        (ProcurementRequest, ProcurementRequest.id.in_(request_ids)),
        (SupplierQuote, SupplierQuote.product_id.in_(product_ids)),
        (AgentRun, AgentRun.workflow_run_id.in_(workflow_ids)),
        (DomainEvent, DomainEvent.idempotency_key.like("demo:%")),
        (AuditEvent, AuditEvent.actor_id == "demo-seeder"),
        (WorkflowRun, WorkflowRun.id.in_(workflow_ids)),
        (Product, Product.id.in_(product_ids)),
        (Supplier, Supplier.source_system == DEMO_SOURCE),
    ):
        session.execute(delete(model).where(condition))
    session.flush()
    return len(products)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage guarded synthetic client-demo data")
    parser.add_argument("command", choices=("seed", "reset", "remove"))
    args = parser.parse_args()
    settings = Settings()
    _guard(settings)
    factory = create_session_factory(create_database_engine(settings))
    with factory.begin() as session:
        removed = remove_demo_data(session, settings) if args.command in {"reset", "remove"} else 0
        created = seed_demo_data(session, settings) if args.command in {"seed", "reset"} else 0
    print(f"Demo data: created={created}, removed={removed}")


if __name__ == "__main__":
    main()
