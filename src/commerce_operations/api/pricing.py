from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from commerce_operations.api.approvals import get_approval_engine
from commerce_operations.api.pricing_schemas import PricingDecisionRequest, PricingDecisionResponse
from commerce_operations.application.pricing import (
    CreatePricingDecision,
    PricingInventoryNotFoundError,
    PricingService,
)
from commerce_operations.approvals.engine import ApprovalEngine
from commerce_operations.config import Settings, get_settings
from commerce_operations.domains.pricing import CommercialPricingRules, PricingValidationError
from commerce_operations.persistence.database import get_session
from commerce_operations.persistence.models import PricingDecision

router = APIRouter(prefix="/pricing-decisions", tags=["pricing"])
SessionDependency = Annotated[Session, Depends(get_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
ApprovalEngineDependency = Annotated[ApprovalEngine, Depends(get_approval_engine)]


def _response(decision: PricingDecision) -> PricingDecisionResponse:
    return PricingDecisionResponse(
        id=decision.id,
        inventory_item_id=decision.inventory_item_id,
        landed_cost=decision.landed_cost,
        landed_cost_source=decision.landed_cost_source,
        marketplace_fees=decision.marketplace_fees,
        fulfilment_costs=decision.fulfilment_costs,
        gross_profit=decision.gross_profit,
        contribution_profit=decision.contribution_profit,
        margin_percentage=decision.margin_percentage,
        roi_percentage=decision.roi_percentage,
        minimum_selling_price=decision.minimum_price,
        recommended_selling_price=decision.recommended_price,
        current_selling_price=decision.current_price,
        price_change_percentage=decision.price_change_percentage,
        currency=decision.currency,
        policy_result=decision.policy_result,
        approval_id=decision.approval_id,
        formula_version=decision.formula_version,
        effective_at=decision.effective_at,
        created_at=decision.created_at,
    )


@router.post("", response_model=PricingDecisionResponse, status_code=status.HTTP_201_CREATED)
def create_pricing_decision(
    request: PricingDecisionRequest,
    session: SessionDependency,
    settings: SettingsDependency,
    approval_engine: ApprovalEngineDependency,
) -> PricingDecisionResponse:
    command = CreatePricingDecision(
        inventory_item_id=request.inventory_item_id,
        landed_cost=request.landed_cost,
        marketplace_fees=request.marketplace_fees,
        fulfilment_costs=request.fulfilment_costs,
        target_margin=request.target_margin
        if request.target_margin is not None
        else Decimal(str(settings.pricing_target_margin)),
        minimum_margin=request.minimum_margin
        if request.minimum_margin is not None
        else Decimal(str(settings.pricing_minimum_margin)),
        current_selling_price=request.current_selling_price,
        currency=request.currency,
        reason=request.reason,
        requester=request.requester,
        rules=CommercialPricingRules(
            minimum_price=request.minimum_price
            if request.minimum_price is not None
            else Decimal(str(settings.pricing_minimum_price)),
            maximum_price=request.maximum_price
            if request.maximum_price is not None
            else (
                Decimal(str(settings.pricing_maximum_price))
                if settings.pricing_maximum_price is not None
                else None
            ),
            rounding_increment=request.rounding_increment
            if request.rounding_increment is not None
            else Decimal(str(settings.pricing_rounding_increment)),
            maximum_automatic_change_percent=Decimal(
                str(settings.significant_price_change_percent)
            ),
        ),
    )
    try:
        decision = PricingService(approval_engine).create_decision(session, command)
        session.commit()
        session.refresh(decision)
    except PricingInventoryNotFoundError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inventory item not found") from exc
    except PricingValidationError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _response(decision)
