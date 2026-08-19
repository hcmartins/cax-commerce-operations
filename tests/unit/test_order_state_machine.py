import pytest

from commerce_operations.domains.orders import InvalidOrderTransition, transition_order
from commerce_operations.persistence.enums import OrderStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PENDING, OrderStatus.PAID),
        (OrderStatus.PENDING, OrderStatus.CANCELLED),
        (OrderStatus.PAID, OrderStatus.PROCESSING),
        (OrderStatus.PAID, OrderStatus.CANCELLED),
        (OrderStatus.PROCESSING, OrderStatus.DISPATCHED),
        (OrderStatus.PROCESSING, OrderStatus.CANCELLED),
        (OrderStatus.DISPATCHED, OrderStatus.DELIVERED),
        (OrderStatus.DISPATCHED, OrderStatus.RETURNED),
        (OrderStatus.DELIVERED, OrderStatus.RETURNED),
        (OrderStatus.DELIVERED, OrderStatus.REFUNDED),
        (OrderStatus.RETURNED, OrderStatus.REFUNDED),
    ],
)
def test_allowed_order_transitions(current, target):
    assert transition_order(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderStatus.PENDING, OrderStatus.DISPATCHED),
        (OrderStatus.PAID, OrderStatus.DELIVERED),
        (OrderStatus.DISPATCHED, OrderStatus.CANCELLED),
        (OrderStatus.CANCELLED, OrderStatus.PAID),
        (OrderStatus.REFUNDED, OrderStatus.PROCESSING),
    ],
)
def test_invalid_order_transitions_are_rejected(current, target):
    with pytest.raises(InvalidOrderTransition):
        transition_order(current, target)


def test_repeated_current_status_is_idempotent():
    assert transition_order(OrderStatus.PAID, OrderStatus.PAID) is OrderStatus.PAID
