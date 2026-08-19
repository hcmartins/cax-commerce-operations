import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalActionType, ApprovalContext
from commerce_operations.domains.pricing import (
    CommercialPricingRules,
    PricingInputs,
    calculate_price,
)
from commerce_operations.persistence.models import (
    Approval,
    AuditEvent,
    InventoryItem,
    PricingDecision,
)


class PricingInventoryNotFoundError(LookupError):
    pass


class PricingApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class CreatePricingDecision:
    inventory_item_id: uuid.UUID
    marketplace_fees: Decimal
    fulfilment_costs: Decimal
    target_margin: Decimal
    minimum_margin: Decimal
    current_selling_price: Decimal | None
    currency: str
    reason: str
    requester: str
    landed_cost: Decimal | None = None
    rules: CommercialPricingRules = CommercialPricingRules()


class PricingService:
    formula_version = 1

    def __init__(self, approval_engine: ApprovalEngine) -> None:
        self.approval_engine = approval_engine

    def create_decision(self, session: Session, command: CreatePricingDecision) -> PricingDecision:
        inventory = session.scalar(
            select(InventoryItem)
            .where(InventoryItem.id == command.inventory_item_id)
            .with_for_update()
        )
        if inventory is None:
            raise PricingInventoryNotFoundError(str(command.inventory_item_id))
        landed_cost = (
            command.landed_cost if command.landed_cost is not None else inventory.cost_basis
        )
        source = "estimated" if command.landed_cost is not None else "actual_inventory"
        result = calculate_price(
            PricingInputs(
                landed_cost=landed_cost,
                marketplace_fees=command.marketplace_fees,
                fulfilment_costs=command.fulfilment_costs,
                target_margin=command.target_margin,
                minimum_margin=command.minimum_margin,
                current_selling_price=command.current_selling_price,
            ),
            command.rules,
        )
        decision = PricingDecision(
            inventory_item_id=inventory.id,
            landed_cost=landed_cost,
            marketplace_fees=command.marketplace_fees,
            fulfilment_costs=command.fulfilment_costs,
            target_margin=command.target_margin,
            minimum_margin=command.minimum_margin,
            minimum_price=result.minimum_selling_price,
            current_price=command.current_selling_price,
            proposed_price=result.recommended_selling_price,
            recommended_price=result.recommended_selling_price,
            contribution_margin=result.contribution_profit,
            gross_profit=result.gross_profit,
            contribution_profit=result.contribution_profit,
            margin_percentage=result.margin_percentage,
            roi_percentage=result.roi_percentage,
            price_change_percentage=result.price_change_percentage,
            landed_cost_source=source,
            commercial_rules={
                key: str(value) if value is not None else None
                for key, value in asdict(command.rules).items()
            },
            currency=command.currency.upper(),
            reason=command.reason,
            policy_result="pending",
            formula_version=self.formula_version,
        )
        session.add(decision)
        session.flush()
        approval = self.approval_engine.request_if_required(
            session,
            ApprovalContext(
                ApprovalActionType.PRICE_CHANGE,
                price_change_percent=result.price_change_percentage,
            ),
            action_type=ApprovalActionType.PRICE_CHANGE.value,
            resource_type="pricing_decision",
            resource_id=decision.id,
            requested_action={
                "current_price": (
                    str(command.current_selling_price)
                    if command.current_selling_price is not None
                    else None
                ),
                "recommended_price": str(result.recommended_selling_price),
                "minimum_price": str(result.minimum_selling_price),
                "currency": decision.currency,
            },
            reason=command.reason,
            requester=command.requester,
            risk_level="financial",
        )
        if approval is None:
            decision.policy_result = "allowed"
            decision.effective_at = datetime.now(UTC)
        else:
            decision.policy_result = "approval_required"
            decision.approval_id = approval.id
        self._audit(session, decision, command.requester)
        session.flush()
        return decision

    @staticmethod
    def approve_price_change(approval: Approval, session: Session) -> None:
        if approval.resource_type != "pricing_decision":
            raise PricingApprovalError("Price approval references the wrong entity type")
        decision = session.scalar(
            select(PricingDecision)
            .where(PricingDecision.id == approval.resource_id)
            .with_for_update()
        )
        if decision is None:
            raise PricingApprovalError("Pricing decision was not found")
        if decision.approval_id != approval.id or decision.policy_result != "approval_required":
            raise PricingApprovalError("Pricing decision is not awaiting this approval")
        if decision.proposed_price < decision.minimum_price:
            raise PricingApprovalError("Approved price would breach the profitability floor")
        decision.policy_result = "approved"
        decision.effective_at = datetime.now(UTC)

    @staticmethod
    def _audit(session: Session, decision: PricingDecision, actor: str) -> None:
        session.add(
            AuditEvent(
                actor_type="user",
                actor_id=actor,
                action="pricing.decision_created",
                resource_type="pricing_decision",
                resource_id=decision.id,
                after_state={
                    "recommended_price": str(decision.recommended_price),
                    "minimum_price": str(decision.minimum_price),
                    "policy_result": decision.policy_result,
                },
                reason=decision.reason,
                correlation_id=decision.id,
            )
        )


def register_pricing_approval_handler(registry: ApprovalActionRegistry) -> None:
    registry.register(ApprovalActionType.PRICE_CHANGE.value, PricingService.approve_price_change)
