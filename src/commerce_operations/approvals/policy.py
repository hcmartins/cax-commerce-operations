from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from commerce_operations.config import Settings


class ApprovalActionType(StrEnum):
    SUPPLIER_PURCHASE = "supplier_purchase"
    FIRST_MARKETPLACE_PUBLICATION = "first_marketplace_publication"
    PRICE_CHANGE = "price_change"
    REFUND = "refund"
    DESTRUCTIVE_ACTION = "destructive_action"
    HIGH_RISK_ACTION = "high_risk_action"
    CUSTOMER_RESPONSE = "customer_response"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True)
class ApprovalContext:
    action_type: ApprovalActionType
    amount: Decimal | None = None
    price_change_percent: Decimal | None = None
    is_first_publication: bool = False
    risk_level: str = "normal"


class ApprovalRule(Protocol):
    name: str
    version: int

    def requires_approval(self, context: ApprovalContext) -> bool: ...


@dataclass(frozen=True)
class ActionRule:
    name: str
    action_types: frozenset[ApprovalActionType]
    version: int = 1

    def requires_approval(self, context: ApprovalContext) -> bool:
        return context.action_type in self.action_types


@dataclass(frozen=True)
class FirstPublicationRule:
    name: str = "first_marketplace_publication"
    version: int = 1

    def requires_approval(self, context: ApprovalContext) -> bool:
        return (
            context.action_type is ApprovalActionType.FIRST_MARKETPLACE_PUBLICATION
            and context.is_first_publication
        )


@dataclass(frozen=True)
class SignificantPriceChangeRule:
    threshold_percent: Decimal
    name: str = "significant_price_change"
    version: int = 1

    def requires_approval(self, context: ApprovalContext) -> bool:
        return (
            context.action_type is ApprovalActionType.PRICE_CHANGE
            and context.price_change_percent is not None
            and abs(context.price_change_percent) >= self.threshold_percent
        )


@dataclass(frozen=True)
class RefundThresholdRule:
    threshold: Decimal
    name: str = "refund_above_threshold"
    version: int = 1

    def requires_approval(self, context: ApprovalContext) -> bool:
        return (
            context.action_type is ApprovalActionType.REFUND
            and context.amount is not None
            and context.amount > self.threshold
        )


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    matched_rule: ApprovalRule | None = None


class ApprovalPolicy:
    def __init__(self, rules: list[ApprovalRule]) -> None:
        self.rules = tuple(rules)

    @classmethod
    def from_settings(cls, settings: Settings) -> "ApprovalPolicy":
        return cls(
            [
                ActionRule(
                    name="financial_and_high_risk_actions",
                    action_types=frozenset(
                        {
                            ApprovalActionType.SUPPLIER_PURCHASE,
                            ApprovalActionType.DESTRUCTIVE_ACTION,
                            ApprovalActionType.HIGH_RISK_ACTION,
                        }
                    ),
                ),
                FirstPublicationRule(),
                SignificantPriceChangeRule(Decimal(str(settings.significant_price_change_percent))),
                RefundThresholdRule(Decimal(str(settings.refund_approval_threshold))),
            ]
        )

    def evaluate(self, context: ApprovalContext) -> PolicyResult:
        for rule in self.rules:
            if rule.requires_approval(context):
                return PolicyResult(PolicyDecision.REQUIRE_APPROVAL, rule)
        return PolicyResult(PolicyDecision.ALLOW)
