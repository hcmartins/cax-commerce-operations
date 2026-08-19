from decimal import Decimal

import pytest

from commerce_operations.approvals.policy import (
    ApprovalActionType,
    ApprovalContext,
    ApprovalPolicy,
    PolicyDecision,
)
from commerce_operations.config import Settings


@pytest.fixture
def policy() -> ApprovalPolicy:
    return ApprovalPolicy.from_settings(
        Settings(
            significant_price_change_percent=10,
            refund_approval_threshold=50,
            _env_file=None,
        )
    )


@pytest.mark.parametrize(
    "context",
    [
        ApprovalContext(ApprovalActionType.SUPPLIER_PURCHASE),
        ApprovalContext(
            ApprovalActionType.FIRST_MARKETPLACE_PUBLICATION,
            is_first_publication=True,
        ),
        ApprovalContext(
            ApprovalActionType.PRICE_CHANGE,
            price_change_percent=Decimal("10"),
        ),
        ApprovalContext(ApprovalActionType.REFUND, amount=Decimal("50.01")),
        ApprovalContext(ApprovalActionType.DESTRUCTIVE_ACTION),
        ApprovalContext(ApprovalActionType.HIGH_RISK_ACTION),
    ],
)
def test_initial_rules_require_approval(policy: ApprovalPolicy, context: ApprovalContext) -> None:
    assert policy.evaluate(context).decision is PolicyDecision.REQUIRE_APPROVAL


def test_below_threshold_action_is_allowed(policy: ApprovalPolicy) -> None:
    context = ApprovalContext(ApprovalActionType.REFUND, amount=Decimal("50.00"))

    assert policy.evaluate(context).decision is PolicyDecision.ALLOW
