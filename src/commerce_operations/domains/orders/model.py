from commerce_operations.persistence.enums import OrderStatus


class InvalidOrderTransition(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.PROCESSING, OrderStatus.CANCELLED}),
    OrderStatus.PROCESSING: frozenset({OrderStatus.DISPATCHED, OrderStatus.CANCELLED}),
    OrderStatus.DISPATCHED: frozenset({OrderStatus.DELIVERED, OrderStatus.RETURNED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.RETURNED, OrderStatus.REFUNDED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.RETURNED: frozenset({OrderStatus.REFUNDED}),
    OrderStatus.REFUNDED: frozenset(),
}


def transition_order(current: OrderStatus, target: OrderStatus) -> OrderStatus:
    if target is current:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidOrderTransition(
            f"Order cannot transition from {current.value} to {target.value}"
        )
    return target
