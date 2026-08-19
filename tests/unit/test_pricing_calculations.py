from decimal import Decimal

import pytest

from commerce_operations.domains.pricing import (
    CommercialPricingRules,
    PricingInputs,
    PricingValidationError,
    calculate_price,
)


def inputs(**overrides):
    values = {
        "landed_cost": Decimal("10"),
        "marketplace_fees": Decimal("2"),
        "fulfilment_costs": Decimal("1"),
        "target_margin": Decimal("0.30"),
        "minimum_margin": Decimal("0.15"),
        "current_selling_price": Decimal("18"),
    }
    values.update(overrides)
    return PricingInputs(**values)


def test_deterministic_profit_margin_roi_and_prices():
    result = calculate_price(inputs(), CommercialPricingRules())

    assert result.minimum_selling_price == Decimal("15.3000")
    assert result.recommended_selling_price == Decimal("18.5800")
    assert result.gross_profit == Decimal("8.5800")
    assert result.contribution_profit == Decimal("5.5800")
    assert result.margin_percentage == Decimal("30.0323")
    assert result.roi_percentage == Decimal("55.8000")
    assert result.price_change_percentage == Decimal("3.2222")
    assert result.within_automatic_boundary is True


def test_rounding_always_moves_up_and_never_breaches_floor():
    result = calculate_price(
        inputs(target_margin=Decimal("0.25"), minimum_margin=Decimal("0.20")),
        CommercialPricingRules(rounding_increment=Decimal("0.05")),
    )
    assert result.minimum_selling_price == Decimal("16.2500")
    assert result.recommended_selling_price == Decimal("17.3500")
    assert result.margin_percentage >= Decimal("25")


def test_commercial_minimum_price_can_raise_recommendation():
    result = calculate_price(inputs(), CommercialPricingRules(minimum_price=Decimal("25")))
    assert result.minimum_selling_price == Decimal("25.0000")
    assert result.recommended_selling_price == Decimal("25.0000")


def test_zero_landed_cost_has_undefined_roi_without_division_error():
    result = calculate_price(
        inputs(
            landed_cost=Decimal("0"),
            marketplace_fees=Decimal("0"),
            fulfilment_costs=Decimal("0"),
            current_selling_price=None,
        ),
        CommercialPricingRules(minimum_price=Decimal("1")),
    )
    assert result.roi_percentage is None
    assert result.price_change_percentage is None
    assert result.recommended_selling_price == Decimal("1.0000")


def test_change_at_safe_boundary_requires_approval():
    result = calculate_price(
        inputs(
            landed_cost=Decimal("7"),
            marketplace_fees=Decimal("0"),
            fulfilment_costs=Decimal("0"),
            target_margin=Decimal("0.30"),
            minimum_margin=Decimal("0.10"),
            current_selling_price=Decimal("9.09"),
        ),
        CommercialPricingRules(maximum_automatic_change_percent=Decimal("10")),
    )
    assert result.recommended_selling_price == Decimal("10.0000")
    assert result.price_change_percentage > Decimal("10")
    assert result.within_automatic_boundary is False


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (inputs(landed_cost=Decimal("-1")), "landed_cost"),
        (inputs(minimum_margin=Decimal("1")), "minimum_margin"),
        (inputs(target_margin=Decimal("0.10"), minimum_margin=Decimal("0.20")), "target_margin"),
        (inputs(current_selling_price=Decimal("0")), "current_selling_price"),
    ],
)
def test_invalid_inputs_are_rejected(candidate, message):
    with pytest.raises(PricingValidationError, match=message):
        calculate_price(candidate, CommercialPricingRules())


def test_maximum_price_cannot_override_profitability_floor():
    with pytest.raises(PricingValidationError, match="cannot satisfy"):
        calculate_price(inputs(), CommercialPricingRules(maximum_price=Decimal("12")))
