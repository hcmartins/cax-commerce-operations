from decimal import Decimal

import pytest

from commerce_operations.domains.inventory import (
    InvalidInventoryChange,
    apply_quantity_change,
    weighted_cost_basis,
)


def test_quantity_change_calculates_available_stock() -> None:
    balance = apply_quantity_change(
        quantity_on_hand=20,
        reserved_quantity=5,
        quantity_delta=-10,
    )

    assert balance.quantity_on_hand == 10
    assert balance.available_quantity == 5


@pytest.mark.parametrize("delta", [0, -16, -30])
def test_invalid_quantity_changes_are_rejected(delta: int) -> None:
    with pytest.raises(InvalidInventoryChange):
        apply_quantity_change(
            quantity_on_hand=20,
            reserved_quantity=5,
            quantity_delta=delta,
        )


def test_weighted_cost_basis() -> None:
    cost = weighted_cost_basis(
        current_quantity=40,
        current_cost_basis=Decimal("5.00"),
        added_quantity=60,
        added_unit_cost=Decimal("7.00"),
    )

    assert cost == Decimal("6.20")


@pytest.mark.parametrize(
    ("quantity", "cost"),
    [(0, Decimal("1")), (-1, Decimal("1")), (1, Decimal("-1"))],
)
def test_invalid_cost_inputs_are_rejected(quantity: int, cost: Decimal) -> None:
    with pytest.raises(InvalidInventoryChange):
        weighted_cost_basis(
            current_quantity=1,
            current_cost_basis=Decimal("1"),
            added_quantity=quantity,
            added_unit_cost=cost,
        )
