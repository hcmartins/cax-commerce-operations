from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.application.pricing import (
    CreatePricingDecision,
    PricingService,
    register_pricing_approval_handler,
)
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.config import Settings
from commerce_operations.domains.pricing import CommercialPricingRules
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import ApprovalStatus
from commerce_operations.persistence.models import Approval, InventoryItem, PricingDecision, Product


def setup_database(tmp_path) -> tuple[sessionmaker[Session], InventoryItem]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'pricing.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id="pricing-product",
            source_workflow_run_id="workflow",
            source_recommendation_id="recommendation",
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
            reserved_quantity=0,
            cost_basis=Decimal("10"),
            currency="GBP",
            low_stock_threshold=2,
        )
        session.add(inventory)
        session.flush()
    return factory, inventory


def engine() -> ApprovalEngine:
    registry = ApprovalActionRegistry()
    register_pricing_approval_handler(registry)
    settings = Settings(
        environment="test",
        significant_price_change_percent=10,
        _env_file=None,
    )
    return ApprovalEngine(ApprovalPolicy.from_settings(settings), action_registry=registry)


def command(inventory_id, current_price):
    return CreatePricingDecision(
        inventory_item_id=inventory_id,
        marketplace_fees=Decimal("2"),
        fulfilment_costs=Decimal("1"),
        target_margin=Decimal("0.30"),
        minimum_margin=Decimal("0.15"),
        current_selling_price=current_price,
        currency="GBP",
        reason="Target contribution margin",
        requester="pricing-manager@example.com",
        rules=CommercialPricingRules(maximum_automatic_change_percent=Decimal("10")),
    )


def test_safe_price_is_effective_without_approval_and_uses_actual_cost(tmp_path):
    factory, inventory = setup_database(tmp_path)
    with factory.begin() as session:
        decision = PricingService(engine()).create_decision(
            session, command(inventory.id, Decimal("18"))
        )
        decision_id = decision.id
    with factory() as session:
        decision = session.get(PricingDecision, decision_id)
        assert decision is not None
        assert decision.landed_cost_source == "actual_inventory"
        assert decision.recommended_price == Decimal("18.5800")
        assert decision.proposed_price >= decision.minimum_price
        assert decision.policy_result == "allowed"
        assert decision.effective_at is not None
        assert session.scalar(select(Approval)) is None
    factory.kw["bind"].dispose()


def test_large_change_creates_approval_and_only_becomes_effective_after_approval(tmp_path):
    factory, inventory = setup_database(tmp_path)
    approvals = engine()
    with factory.begin() as session:
        decision = PricingService(approvals).create_decision(
            session, command(inventory.id, Decimal("10"))
        )
        assert decision.policy_result == "approval_required"
        assert decision.effective_at is None
        approval_id = decision.approval_id
        decision_id = decision.id
    with factory.begin() as session:
        approval = approvals.approve(
            session,
            approval_id,
            approver="commercial-director@example.com",
            reason="Margin and market position reviewed",
        )
        assert approval.status is ApprovalStatus.APPROVED
    with factory() as session:
        decision = session.get(PricingDecision, decision_id)
        assert decision is not None
        assert decision.policy_result == "approved"
        assert decision.effective_at is not None
        assert decision.proposed_price >= decision.minimum_price
    factory.kw["bind"].dispose()
