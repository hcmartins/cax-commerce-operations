import pytest

from commerce_operations.domains.procurement import (
    InvalidProcurementTransition,
    transition,
)
from commerce_operations.persistence.enums import ProcurementStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ProcurementStatus.PROPOSED, ProcurementStatus.AWAITING_APPROVAL),
        (ProcurementStatus.AWAITING_APPROVAL, ProcurementStatus.APPROVED),
        (ProcurementStatus.APPROVED, ProcurementStatus.ORDERED),
        (ProcurementStatus.ORDERED, ProcurementStatus.SHIPPED),
        (ProcurementStatus.SHIPPED, ProcurementStatus.RECEIVED),
        (ProcurementStatus.PROPOSED, ProcurementStatus.CANCELLED),
        (ProcurementStatus.AWAITING_APPROVAL, ProcurementStatus.CANCELLED),
        (ProcurementStatus.APPROVED, ProcurementStatus.CANCELLED),
    ],
)
def test_allowed_procurement_transitions(current, target) -> None:
    result = transition(current, target)

    assert result.previous is current
    assert result.current is target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ProcurementStatus.PROPOSED, ProcurementStatus.ORDERED),
        (ProcurementStatus.AWAITING_APPROVAL, ProcurementStatus.SHIPPED),
        (ProcurementStatus.RECEIVED, ProcurementStatus.CANCELLED),
        (ProcurementStatus.ORDERED, ProcurementStatus.CANCELLED),
        (ProcurementStatus.SHIPPED, ProcurementStatus.CANCELLED),
        (ProcurementStatus.CANCELLED, ProcurementStatus.PROPOSED),
    ],
)
def test_invalid_procurement_transitions_are_rejected(current, target) -> None:
    with pytest.raises(InvalidProcurementTransition):
        transition(current, target)
