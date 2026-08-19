from dataclasses import dataclass

from commerce_operations.persistence.enums import ProcurementStatus


class InvalidProcurementTransition(RuntimeError):
    pass


ALLOWED_TRANSITIONS: dict[ProcurementStatus, frozenset[ProcurementStatus]] = {
    ProcurementStatus.PROPOSED: frozenset(
        {ProcurementStatus.AWAITING_APPROVAL, ProcurementStatus.CANCELLED}
    ),
    ProcurementStatus.AWAITING_APPROVAL: frozenset(
        {ProcurementStatus.APPROVED, ProcurementStatus.CANCELLED}
    ),
    ProcurementStatus.APPROVED: frozenset({ProcurementStatus.ORDERED, ProcurementStatus.CANCELLED}),
    ProcurementStatus.ORDERED: frozenset({ProcurementStatus.SHIPPED}),
    ProcurementStatus.SHIPPED: frozenset({ProcurementStatus.RECEIVED}),
    ProcurementStatus.RECEIVED: frozenset(),
    ProcurementStatus.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class ProcurementTransition:
    previous: ProcurementStatus
    current: ProcurementStatus


def transition(current: ProcurementStatus, target: ProcurementStatus) -> ProcurementTransition:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidProcurementTransition(
            f"Cannot transition procurement from {current.value} to {target.value}"
        )
    return ProcurementTransition(current, target)
