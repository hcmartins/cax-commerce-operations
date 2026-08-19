from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

MONEY_PLACES = Decimal("0.0001")
PERCENT_PLACES = Decimal("0.0001")


class PricingValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CommercialPricingRules:
    minimum_price: Decimal = Decimal("0")
    maximum_price: Decimal | None = None
    rounding_increment: Decimal = Decimal("0.01")
    maximum_automatic_change_percent: Decimal = Decimal("10")


@dataclass(frozen=True)
class PricingInputs:
    landed_cost: Decimal
    marketplace_fees: Decimal
    fulfilment_costs: Decimal
    target_margin: Decimal
    minimum_margin: Decimal
    current_selling_price: Decimal | None


@dataclass(frozen=True)
class PricingCalculation:
    gross_profit: Decimal
    contribution_profit: Decimal
    margin_percentage: Decimal
    roi_percentage: Decimal | None
    minimum_selling_price: Decimal
    recommended_selling_price: Decimal
    price_change_percentage: Decimal | None
    within_automatic_boundary: bool


def calculate_price(inputs: PricingInputs, rules: CommercialPricingRules) -> PricingCalculation:
    _validate(inputs, rules)
    total_cost = inputs.landed_cost + inputs.marketplace_fees + inputs.fulfilment_costs
    floor_from_margin = total_cost / (Decimal("1") - inputs.minimum_margin)
    target_price = total_cost / (Decimal("1") - inputs.target_margin)
    minimum_price = _round_up(max(floor_from_margin, rules.minimum_price), rules.rounding_increment)
    recommended_price = _round_up(max(target_price, minimum_price), rules.rounding_increment)
    if rules.maximum_price is not None and recommended_price > rules.maximum_price:
        raise PricingValidationError(
            "Configured maximum price cannot satisfy the profitability floor and target margin"
        )

    gross_profit = recommended_price - inputs.landed_cost
    contribution_profit = recommended_price - total_cost
    margin_percentage = contribution_profit / recommended_price * Decimal("100")
    roi_percentage = (
        contribution_profit / inputs.landed_cost * Decimal("100")
        if inputs.landed_cost > 0
        else None
    )
    price_change = (
        (recommended_price - inputs.current_selling_price)
        / inputs.current_selling_price
        * Decimal("100")
        if inputs.current_selling_price is not None
        else None
    )
    within_boundary = (
        price_change is None or abs(price_change) < rules.maximum_automatic_change_percent
    )
    return PricingCalculation(
        gross_profit=_money(gross_profit),
        contribution_profit=_money(contribution_profit),
        margin_percentage=margin_percentage.quantize(PERCENT_PLACES),
        roi_percentage=(
            roi_percentage.quantize(PERCENT_PLACES) if roi_percentage is not None else None
        ),
        minimum_selling_price=_money(minimum_price),
        recommended_selling_price=_money(recommended_price),
        price_change_percentage=(
            price_change.quantize(PERCENT_PLACES) if price_change is not None else None
        ),
        within_automatic_boundary=within_boundary,
    )


def _validate(inputs: PricingInputs, rules: CommercialPricingRules) -> None:
    for name in ("landed_cost", "marketplace_fees", "fulfilment_costs"):
        if getattr(inputs, name) < 0:
            raise PricingValidationError(f"{name} cannot be negative")
    if not Decimal("0") <= inputs.minimum_margin < Decimal("1"):
        raise PricingValidationError("minimum_margin must be at least 0 and below 1")
    if not inputs.minimum_margin <= inputs.target_margin < Decimal("1"):
        raise PricingValidationError("target_margin must be at least minimum_margin and below 1")
    if inputs.current_selling_price is not None and inputs.current_selling_price <= 0:
        raise PricingValidationError("current_selling_price must be positive when supplied")
    if rules.minimum_price < 0:
        raise PricingValidationError("configured minimum_price cannot be negative")
    if rules.maximum_price is not None and rules.maximum_price < rules.minimum_price:
        raise PricingValidationError("maximum_price cannot be below minimum_price")
    if rules.rounding_increment <= 0:
        raise PricingValidationError("rounding_increment must be positive")
    if rules.maximum_automatic_change_percent <= 0:
        raise PricingValidationError("maximum automatic change must be positive")


def _round_up(value: Decimal, increment: Decimal) -> Decimal:
    units = (value / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_PLACES)
