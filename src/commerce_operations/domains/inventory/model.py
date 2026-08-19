from dataclasses import dataclass
from decimal import Decimal


class InvalidInventoryChange(RuntimeError):
    pass


@dataclass(frozen=True)
class InventoryBalance:
    quantity_on_hand: int
    reserved_quantity: int

    @property
    def available_quantity(self) -> int:
        return self.quantity_on_hand - self.reserved_quantity


def apply_quantity_change(
    *, quantity_on_hand: int, reserved_quantity: int, quantity_delta: int
) -> InventoryBalance:
    if quantity_delta == 0:
        raise InvalidInventoryChange("Inventory change must not be zero")
    new_on_hand = quantity_on_hand + quantity_delta
    if new_on_hand < 0:
        raise InvalidInventoryChange("Inventory change would make on-hand stock negative")
    if new_on_hand < reserved_quantity:
        raise InvalidInventoryChange("Inventory change would make available stock negative")
    return InventoryBalance(new_on_hand, reserved_quantity)


def weighted_cost_basis(
    *,
    current_quantity: int,
    current_cost_basis: Decimal,
    added_quantity: int,
    added_unit_cost: Decimal,
) -> Decimal:
    if added_quantity <= 0:
        raise InvalidInventoryChange("Cost basis can only be recalculated for positive stock")
    if added_unit_cost < 0:
        raise InvalidInventoryChange("Unit cost cannot be negative")
    total_quantity = current_quantity + added_quantity
    return (
        current_cost_basis * current_quantity + added_unit_cost * added_quantity
    ) / total_quantity
