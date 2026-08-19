"""SQLAlchemy persistence mappings. These are not HTTP request/response schemas."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PythonEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from commerce_operations.persistence.base import Base, EntityMixin
from commerce_operations.persistence.enums import (
    ApprovalStatus,
    ConversationStatus,
    CustomerIntent,
    CustomerServiceDecision,
    EventStatus,
    HandlerReceiptStatus,
    InventoryMovementType,
    MessageDirection,
    MessageStatus,
    OrderStatus,
    ProcurementStatus,
    ProductStatus,
    PublicationStatus,
    PurchaseOrderStatus,
    RefundStatus,
    ReturnStatus,
    RunStatus,
    SupplierStatus,
)

MONEY = Numeric(19, 4)
PERCENT = Numeric(9, 4)


def enum_type(enum: type[PythonEnum], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Product(EntityMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("source_system", "external_product_id"),)

    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_workflow_run_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_recommendation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    identifiers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recommendation_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[ProductStatus] = mapped_column(
        enum_type(ProductStatus, "product_status"), default=ProductStatus.APPROVED, index=True
    )

    supplier_quotes: Mapped[list["SupplierQuote"]] = relationship(back_populates="product")
    procurement_requests: Mapped[list["ProcurementRequest"]] = relationship(
        back_populates="product"
    )
    inventory_items: Mapped[list["InventoryItem"]] = relationship(back_populates="product")


class Supplier(EntityMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("source_system", "external_supplier_id"),)

    source_system: Mapped[str] = mapped_column(String(100), nullable=False)
    external_supplier_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    contact_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[SupplierStatus] = mapped_column(
        enum_type(SupplierStatus, "supplier_status"), default=SupplierStatus.ACTIVE
    )

    quotes: Mapped[list["SupplierQuote"]] = relationship(back_populates="supplier")
    purchase_orders: Mapped[list["PurchaseOrder"]] = relationship(back_populates="supplier")


class SupplierQuote(EntityMixin, Base):
    __tablename__ = "supplier_quotes"
    __table_args__ = (
        UniqueConstraint("supplier_id", "external_quote_id"),
        CheckConstraint("moq > 0", name="positive_moq"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("unit_cost >= 0", name="nonnegative_unit_cost"),
        CheckConstraint("shipping_cost >= 0", name="nonnegative_shipping_cost"),
        CheckConstraint("lead_time_days >= 0", name="nonnegative_lead_time"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    external_quote_id: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    moq: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    shipping_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    source_reference: Mapped[str | None] = mapped_column(String(500))

    product: Mapped[Product] = relationship(back_populates="supplier_quotes")
    supplier: Mapped[Supplier] = relationship(back_populates="quotes")


class ProcurementRequest(EntityMixin, Base):
    __tablename__ = "procurement_requests"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="positive_requested_quantity"),
        CheckConstraint("estimated_landed_cost >= 0", name="nonnegative_estimated_landed_cost"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    selected_quote_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("supplier_quotes.id"))
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id"), unique=True
    )
    requested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_landed_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    recommendation_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expected_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ProcurementStatus] = mapped_column(
        enum_type(ProcurementStatus, "procurement_status"),
        default=ProcurementStatus.PROPOSED,
        index=True,
    )

    product: Mapped[Product] = relationship(back_populates="procurement_requests")
    selected_quote: Mapped[SupplierQuote] = relationship()
    workflow_run: Mapped["WorkflowRun | None"] = relationship()
    purchase_order: Mapped["PurchaseOrder | None"] = relationship(
        back_populates="procurement_request"
    )


class PurchaseOrder(EntityMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("received_quantity >= 0", name="nonnegative_received_quantity"),
        CheckConstraint("received_quantity <= quantity", name="received_within_ordered"),
        CheckConstraint("total_amount >= 0", name="nonnegative_total"),
    )

    procurement_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("procurement_requests.id"), unique=True
    )
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    po_number: Mapped[str] = mapped_column(String(100), unique=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    received_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    actual_landed_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    terms: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    external_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        enum_type(PurchaseOrderStatus, "purchase_order_status"),
        default=PurchaseOrderStatus.DRAFT,
        index=True,
    )
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    procurement_request: Mapped[ProcurementRequest] = relationship(back_populates="purchase_order")
    supplier: Mapped[Supplier] = relationship(back_populates="purchase_orders")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="purchase_order"
    )


class InventoryItem(EntityMixin, Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("sku", "storage_location"),
        CheckConstraint("quantity_on_hand >= 0", name="nonnegative_on_hand"),
        CheckConstraint("reserved_quantity >= 0", name="nonnegative_reserved"),
        CheckConstraint("reserved_quantity <= quantity_on_hand", name="reservation_within_stock"),
        CheckConstraint("cost_basis >= 0", name="nonnegative_cost_basis"),
        CheckConstraint("low_stock_threshold >= 0", name="nonnegative_low_stock_threshold"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    storage_location: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, default=0)
    cost_basis: Mapped[Decimal] = mapped_column(MONEY, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.reserved_quantity

    product: Mapped[Product] = relationship(back_populates="inventory_items")
    movements: Mapped[list["InventoryMovement"]] = relationship(back_populates="inventory_item")
    listing_drafts: Mapped[list["ListingDraft"]] = relationship(back_populates="inventory_item")


class InventoryMovement(EntityMixin, Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint("quantity_delta != 0", name="nonzero_quantity_delta"),
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), index=True
    )
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id"))
    movement_type: Mapped[InventoryMovementType] = mapped_column(
        enum_type(InventoryMovementType, "inventory_movement_type"), index=True
    )
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="movements")
    purchase_order: Mapped[PurchaseOrder | None] = relationship(
        back_populates="inventory_movements"
    )


class ListingDraft(EntityMixin, Base):
    __tablename__ = "listing_drafts"
    __table_args__ = (
        UniqueConstraint("inventory_item_id", "marketplace", "listing_version"),
        CheckConstraint("listing_version > 0", name="positive_listing_version"),
        CheckConstraint("proposed_price >= 0", name="nonnegative_proposed_price"),
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), index=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"), unique=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"), unique=True)
    marketplace: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    listing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    bullet_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(255))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    search_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    proposed_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    image_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    marketplace_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation_results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "listing_approval_status"), default=ApprovalStatus.REQUESTED
    )
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    structured_ai_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    ai_provider: Mapped[str | None] = mapped_column(String(100))
    ai_model: Mapped[str | None] = mapped_column(String(255))

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="listing_drafts")
    workflow_run: Mapped["WorkflowRun | None"] = relationship()
    agent_run: Mapped["AgentRun | None"] = relationship()
    approval: Mapped["Approval | None"] = relationship()
    marketplace_listing: Mapped["MarketplaceListing | None"] = relationship(back_populates="draft")


class MarketplaceListing(EntityMixin, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        UniqueConstraint(
            "marketplace",
            "marketplace_account_id",
            "external_listing_id",
            name="uq_marketplace_listing_external_id",
        ),
        UniqueConstraint(
            "marketplace",
            "marketplace_account_id",
            "sku",
            name="uq_marketplace_listing_sku",
        ),
    )

    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listing_drafts.id"), unique=True)
    marketplace: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    external_listing_id: Mapped[str | None] = mapped_column(String(255))
    publication_status: Mapped[PublicationStatus] = mapped_column(
        enum_type(PublicationStatus, "publication_status"),
        default=PublicationStatus.DRAFT,
        index=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synchronised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    draft: Mapped[ListingDraft] = relationship(back_populates="marketplace_listing")
    pricing_decisions: Mapped[list["PricingDecision"]] = relationship(back_populates="listing")


class Approval(EntityMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("resource_type", "resource_id", "action_hash"),)

    action_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    requested_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(50), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING, index=True
    )
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_reason: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    rationale: Mapped[str | None] = mapped_column(Text)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id"), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow_run: Mapped["WorkflowRun | None"] = relationship()


class PricingDecision(EntityMixin, Base):
    __tablename__ = "pricing_decisions"
    __table_args__ = (
        CheckConstraint("landed_cost >= 0", name="nonnegative_landed_cost"),
        CheckConstraint("fulfilment_costs >= 0", name="nonnegative_fulfilment_costs"),
        CheckConstraint("minimum_price >= 0", name="nonnegative_minimum_price"),
        CheckConstraint("proposed_price >= minimum_price", name="price_above_floor"),
        CheckConstraint("minimum_margin >= 0 AND minimum_margin < 1", name="valid_minimum_margin"),
        CheckConstraint(
            "target_margin >= minimum_margin AND target_margin < 1", name="valid_target_margin"
        ),
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), index=True
    )
    listing_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("marketplace_listings.id"))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    landed_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    marketplace_fees: Mapped[Decimal] = mapped_column(MONEY, default=0)
    fulfilment_costs: Mapped[Decimal] = mapped_column(MONEY, default=0)
    target_margin: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    minimum_margin: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    minimum_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(MONEY)
    proposed_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    contribution_margin: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    gross_profit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    contribution_profit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    margin_percentage: Mapped[Decimal] = mapped_column(PERCENT, nullable=False)
    roi_percentage: Mapped[Decimal | None] = mapped_column(PERCENT)
    recommended_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price_change_percentage: Mapped[Decimal | None] = mapped_column(PERCENT)
    landed_cost_source: Mapped[str] = mapped_column(String(50), nullable=False)
    commercial_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_result: Mapped[str] = mapped_column(String(50), nullable=False)
    formula_version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    inventory_item: Mapped[InventoryItem] = relationship()
    listing: Mapped[MarketplaceListing | None] = relationship(back_populates="pricing_decisions")
    approval: Mapped[Approval | None] = relationship()


class Order(EntityMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint(
            "marketplace",
            "marketplace_account_id",
            "external_order_id",
            name="uq_orders_marketplace",
        ),
        UniqueConstraint(
            "marketplace",
            "marketplace_account_id",
            "source_event_id",
            name="uq_orders_source_event",
        ),
        CheckConstraint("total_amount >= 0", name="nonnegative_total"),
    )

    marketplace: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id"), index=True
    )
    marketplace_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        enum_type(OrderStatus, "order_status"), default=OrderStatus.PENDING, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    customer_reference: Mapped[str | None] = mapped_column(String(255))
    shipping_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ordered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    returns: Mapped[list["Return"]] = relationship(back_populates="order")
    refunds: Mapped[list["Refund"]] = relationship(back_populates="order")
    conversations: Mapped[list["CustomerConversation"]] = relationship(back_populates="order")
    workflow_run: Mapped["WorkflowRun | None"] = relationship()


class OrderItem(EntityMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "external_line_id"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("unit_price >= 0", name="nonnegative_unit_price"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inventory_items.id"))
    external_line_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    reservation_reference: Mapped[str | None] = mapped_column(String(255))

    order: Mapped[Order] = relationship(back_populates="items")
    inventory_item: Mapped[InventoryItem] = relationship()


class Return(EntityMixin, Base):
    __tablename__ = "returns"
    __table_args__ = (CheckConstraint("quantity > 0", name="positive_quantity"),)

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    order_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("order_items.id"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    disposition: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[ReturnStatus] = mapped_column(
        enum_type(ReturnStatus, "return_status"), default=ReturnStatus.REQUESTED, index=True
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="returns")
    order_item: Mapped[OrderItem | None] = relationship()
    refunds: Mapped[list["Refund"]] = relationship(back_populates="return_record")


class Refund(EntityMixin, Base):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("marketplace", "marketplace_account_id", "external_refund_id"),
        CheckConstraint("amount > 0", name="positive_amount"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), index=True)
    return_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("returns.id"))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    marketplace: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_refund_id: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RefundStatus] = mapped_column(
        enum_type(RefundStatus, "refund_status"), default=RefundStatus.PROPOSED, index=True
    )

    order: Mapped[Order] = relationship(back_populates="refunds")
    return_record: Mapped[Return | None] = relationship(back_populates="refunds")
    approval: Mapped[Approval | None] = relationship()


class CustomerConversation(EntityMixin, Base):
    __tablename__ = "customer_conversations"
    __table_args__ = (
        UniqueConstraint("marketplace", "marketplace_account_id", "external_conversation_id"),
    )

    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"))
    marketplace: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    customer_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    classification: Mapped[str | None] = mapped_column(String(100), index=True)
    risk_level: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[ConversationStatus] = mapped_column(
        enum_type(ConversationStatus, "conversation_status"),
        default=ConversationStatus.OPEN,
        index=True,
    )
    assignee: Mapped[str | None] = mapped_column(String(255))

    order: Mapped[Order | None] = relationship(back_populates="conversations")
    product: Mapped[Product | None] = relationship()
    messages: Mapped[list["CustomerMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class CustomerMessage(EntityMixin, Base):
    __tablename__ = "customer_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "external_message_id"),
        CheckConstraint("length(content) > 0", name="nonempty_content"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customer_conversations.id"), index=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    direction: Mapped[MessageDirection] = mapped_column(
        enum_type(MessageDirection, "message_direction")
    )
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        enum_type(MessageStatus, "message_status"), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intent: Mapped[CustomerIntent | None] = mapped_column(
        enum_type(CustomerIntent, "customer_intent"), index=True
    )
    classification: Mapped[CustomerServiceDecision | None] = mapped_column(
        enum_type(CustomerServiceDecision, "customer_service_decision"), index=True
    )
    risk_level: Mapped[str | None] = mapped_column(String(50))
    risk_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    generated_response: Mapped[str | None] = mapped_column(Text)
    final_response: Mapped[str | None] = mapped_column(Text)
    structured_ai_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    ai_provider: Mapped[str | None] = mapped_column(String(100))
    ai_model: Mapped[str | None] = mapped_column(String(255))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_cost_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    ai_cost_currency: Mapped[str | None] = mapped_column(String(3))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[CustomerConversation] = relationship(back_populates="messages")
    approval: Mapped[Approval | None] = relationship()


class WorkflowRun(EntityMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (UniqueConstraint("workflow_name", "correlation_id"),)

    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus, "workflow_run_status"), default=RunStatus.PENDING, index=True
    )
    current_step: Mapped[str | None] = mapped_column(String(255))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checkpoints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_approval_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    cost_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    cost_currency: Mapped[str | None] = mapped_column(String(3))

    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="workflow_run")


class ReorderRecommendation(EntityMixin, Base):
    __tablename__ = "reorder_recommendations"
    __table_args__ = (
        UniqueConstraint("source_event_id"),
        CheckConstraint("available_quantity >= 0", name="nonnegative_available_quantity"),
        CheckConstraint("low_stock_threshold >= 0", name="nonnegative_reorder_threshold"),
        CheckConstraint("suggested_quantity > 0", name="positive_suggested_quantity"),
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inventory_items.id"), index=True
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    source_event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="proposed", index=True)

    inventory_item: Mapped[InventoryItem] = relationship()
    workflow_run: Mapped[WorkflowRun] = relationship()


class AgentRun(EntityMixin, Base):
    __tablename__ = "agent_runs"

    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id"), index=True
    )
    agent_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(
        enum_type(RunStatus, "agent_run_status"), default=RunStatus.PENDING, index=True
    )
    input_reference: Mapped[str | None] = mapped_column(String(500))
    output_reference: Mapped[str | None] = mapped_column(String(500))
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    cost_currency: Mapped[str | None] = mapped_column(String(3))
    safety_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    workflow_run: Mapped[WorkflowRun | None] = relationship(back_populates="agent_runs")


class DomainEvent(EntityMixin, Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_version", "event_type"),
        Index("ix_domain_events_outbox", "publication_status", "created_at"),
    )

    event_type: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    producer: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    causation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    publication_status: Mapped[EventStatus] = mapped_column(
        enum_type(EventStatus, "event_status"), default=EventStatus.PENDING
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publication_attempts: Mapped[int] = mapped_column(Integer, default=0)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)

    handler_receipts: Mapped[list["EventHandlerReceipt"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class EventHandlerReceipt(EntityMixin, Base):
    __tablename__ = "event_handler_receipts"
    __table_args__ = (UniqueConstraint("event_id", "handler_name"),)

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domain_events.id", ondelete="CASCADE"), index=True
    )
    handler_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[HandlerReceiptStatus] = mapped_column(
        enum_type(HandlerReceiptStatus, "handler_receipt_status"),
        default=HandlerReceiptStatus.COMPLETED,
    )

    event: Mapped[DomainEvent] = relationship(back_populates="handler_receipts")


class AuditEvent(EntityMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_resource", "resource_type", "resource_id", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
