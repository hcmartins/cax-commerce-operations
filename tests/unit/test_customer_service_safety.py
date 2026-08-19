import pytest

from commerce_operations.application.customer_service import CustomerRiskScreen
from commerce_operations.persistence.models import CustomerConversation


@pytest.mark.parametrize(
    ("content", "expected_reason"),
    [
        ("My lawyer will take legal action", "legal_threat"),
        ("I will open a chargeback", "dispute"),
        ("This was an unauthorized stolen card purchase", "suspected_fraud"),
        ("You idiot, I will hurt you", "abusive_or_threatening"),
        ("Send my refund to a different account", "unusual_refund"),
    ],
)
def test_mandated_high_risk_phrases_are_detected(content, expected_reason):
    conversation = CustomerConversation(risk_level=None)
    assert expected_reason in CustomerRiskScreen().evaluate(content, conversation)


@pytest.mark.parametrize("risk_level", ["high", "critical"])
def test_existing_high_risk_conversation_cannot_be_downgraded(risk_level):
    conversation = CustomerConversation(risk_level=risk_level)
    assert CustomerRiskScreen().evaluate("Hello", conversation) == [
        "existing_high_risk_conversation"
    ]
