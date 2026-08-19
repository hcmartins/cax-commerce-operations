from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.agents.customer_service_models import CustomerServiceAIResponse
from commerce_operations.ai import LLMResult, LLMUsage
from commerce_operations.application.customer_service import (
    CustomerMessageConflict,
    CustomerServiceAgent,
    register_customer_response_approval_handler,
)
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalPolicy
from commerce_operations.config import Settings
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import (
    ConversationStatus,
    CustomerIntent,
    CustomerServiceDecision,
    MessageStatus,
    OrderStatus,
)
from commerce_operations.persistence.models import (
    Approval,
    CustomerConversation,
    CustomerMessage,
    DomainEvent,
    InventoryItem,
    Order,
    OrderItem,
    Product,
)


class MockCustomerServiceLLM:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.requests = []

    def generate(self, request, response_model):
        self.requests.append(request)
        assert response_model is CustomerServiceAIResponse
        return LLMResult(
            output=self.outputs.pop(0),
            provider="mock-provider",
            model="mock-support-model",
            usage=LLMUsage(
                input_tokens=50,
                output_tokens=20,
                cost_amount=Decimal("0.0012"),
                cost_currency="USD",
            ),
        )


@pytest.fixture
def customer_service_database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'customer-service.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="test",
            external_product_id="support-product",
            source_workflow_run_id="workflow",
            source_recommendation_id="recommendation",
            source_payload_hash="hash",
            name="Insulated Bottle",
            brand="Example Brand",
            attributes={"material": "steel", "capacity": "750ml"},
        )
        session.add(product)
        session.flush()
        inventory = InventoryItem(
            product_id=product.id,
            sku="BOTTLE-1",
            storage_location="warehouse",
            quantity_on_hand=5,
            reserved_quantity=1,
            cost_basis=Decimal("5"),
            currency="GBP",
            low_stock_threshold=1,
        )
        session.add(inventory)
        session.flush()
        order = Order(
            marketplace="ebay",
            marketplace_account_id="seller-1",
            external_order_id="external-order-1",
            source_event_id="order-event-1",
            source_payload_hash="order-hash",
            status=OrderStatus.DISPATCHED,
            currency="GBP",
            total_amount=Decimal("14.99"),
            customer_reference="private-customer-reference",
            shipping_details={"name": "Private Name", "address": "Private Address"},
            ordered_at=datetime.now(UTC),
            dispatched_at=datetime.now(UTC),
        )
        session.add(order)
        session.flush()
        order.items.append(
            OrderItem(
                inventory_item_id=inventory.id,
                external_line_id="line-1",
                sku="BOTTLE-1",
                quantity=1,
                unit_price=Decimal("14.99"),
                tax_amount=Decimal("0"),
                discount_amount=Decimal("0"),
            )
        )
        conversation = CustomerConversation(
            order_id=order.id,
            product_id=product.id,
            marketplace="ebay",
            marketplace_account_id="seller-1",
            external_conversation_id="conversation-1",
            customer_reference="private-customer-reference",
            status=ConversationStatus.OPEN,
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
    yield factory, conversation_id
    engine.dispose()


def approval_engine():
    registry = ApprovalActionRegistry()
    register_customer_response_approval_handler(registry)
    settings = Settings(environment="test", _env_file=None)
    return ApprovalEngine(ApprovalPolicy.from_settings(settings), action_registry=registry)


def output(intent, decision, response="Helpful response", risk="low"):
    return CustomerServiceAIResponse(
        intent=intent,
        decision=decision,
        risk_level=risk,
        generated_response=response,
        rationale="Mock classification",
    )


def test_low_risk_order_question_auto_responds_with_minimum_context(
    customer_service_database,
):
    factory, conversation_id = customer_service_database
    llm = MockCustomerServiceLLM(
        output(CustomerIntent.ORDER_STATUS, CustomerServiceDecision.AUTO_RESPOND)
    )
    with factory.begin() as session:
        result = CustomerServiceAgent(
            llm, approval_engine(), prompt_version="support-v3"
        ).handle_message(
            session,
            conversation_id,
            external_message_id="message-1",
            channel="ebay-messaging",
            content="Where is my order?",
        )
        message_id = result.message.id
    assert len(llm.requests) == 1
    context = llm.requests[0].context
    assert context["order"]["status"] == "dispatched"
    assert "customer_reference" not in context["order"]
    assert "shipping_details" not in context["order"]
    assert "private-customer-reference" not in str(context)
    assert "Private Address" not in str(context)
    with factory() as session:
        message = session.get(CustomerMessage, message_id)
        assert message is not None
        assert message.classification is CustomerServiceDecision.AUTO_RESPOND
        assert message.final_response == "Helpful response"
        assert message.status is MessageStatus.APPROVED
        assert message.prompt_version == "support-v3"
        assert message.ai_provider == "mock-provider"
        assert message.input_tokens == 50
        assert message.ai_cost_amount == Decimal("0.0012")


def test_return_request_is_drafted_and_resumed_after_human_approval(
    customer_service_database,
):
    factory, conversation_id = customer_service_database
    llm = MockCustomerServiceLLM(
        output(CustomerIntent.RETURN_REQUEST, CustomerServiceDecision.AUTO_RESPOND)
    )
    approvals = approval_engine()
    with factory.begin() as session:
        message = (
            CustomerServiceAgent(llm, approvals)
            .handle_message(
                session,
                conversation_id,
                external_message_id="return-message",
                channel="ebay-messaging",
                content="I would like to return this item.",
            )
            .message
        )
        assert message.classification is CustomerServiceDecision.DRAFT_FOR_APPROVAL
        assert message.status is MessageStatus.DRAFT
        approval_id = message.approval_id
        message_id = message.id
    with factory.begin() as session:
        approvals.approve(
            session,
            approval_id,
            approver="support-lead@example.com",
            reason="Return guidance reviewed",
        )
    with factory() as session:
        message = session.get(CustomerMessage, message_id)
        assert message is not None
        assert message.final_response == "Helpful response"
        assert message.status is MessageStatus.APPROVED
        assert message.responded_at is not None
        assert message.conversation.status is ConversationStatus.OPEN


def test_legal_threat_escalates_without_calling_ai(customer_service_database):
    factory, conversation_id = customer_service_database
    llm = MockCustomerServiceLLM()
    with factory.begin() as session:
        message = (
            CustomerServiceAgent(llm, approval_engine())
            .handle_message(
                session,
                conversation_id,
                external_message_id="legal-message",
                channel="ebay-messaging",
                content="My solicitor will take legal action in court.",
            )
            .message
        )
        assert message.classification is CustomerServiceDecision.HUMAN_ESCALATION
        assert "legal_threat" in message.risk_reasons
        assert message.final_response is None
        assert message.conversation.status is ConversationStatus.ESCALATED
    assert llm.requests == []


def test_unknown_policy_and_ai_high_risk_cannot_auto_respond(customer_service_database):
    factory, conversation_id = customer_service_database
    llm = MockCustomerServiceLLM(
        output(CustomerIntent.UNKNOWN, CustomerServiceDecision.AUTO_RESPOND),
        output(
            CustomerIntent.PRODUCT_ENQUIRY,
            CustomerServiceDecision.AUTO_RESPOND,
            risk="high",
        ),
    )
    agent = CustomerServiceAgent(llm, approval_engine())
    with factory.begin() as session:
        first = agent.handle_message(
            session,
            conversation_id,
            external_message_id="unknown-message",
            channel="ebay-messaging",
            content="Can you arrange a service you do not offer?",
        ).message
        assert first.classification is CustomerServiceDecision.HUMAN_ESCALATION
        assert first.risk_reasons == ["outside_defined_policy"]
    with factory.begin() as session:
        second = agent.handle_message(
            session,
            conversation_id,
            external_message_id="high-risk-message",
            channel="ebay-messaging",
            content="Tell me about the bottle.",
        ).message
        # Existing escalated risk is deterministic and bypasses the second AI output.
        assert second.classification is CustomerServiceDecision.HUMAN_ESCALATION
        assert "existing_high_risk_conversation" in second.risk_reasons
    assert len(llm.requests) == 1


def test_duplicate_message_is_idempotent_and_changed_redelivery_conflicts(
    customer_service_database,
):
    factory, conversation_id = customer_service_database
    llm = MockCustomerServiceLLM(
        output(CustomerIntent.PRODUCT_ENQUIRY, CustomerServiceDecision.AUTO_RESPOND)
    )
    agent = CustomerServiceAgent(llm, approval_engine())
    kwargs = {
        "external_message_id": "duplicate-message",
        "channel": "ebay-messaging",
        "content": "What material is it?",
    }
    with factory.begin() as session:
        first = agent.handle_message(session, conversation_id, **kwargs)
    with factory.begin() as session:
        duplicate = agent.handle_message(session, conversation_id, **kwargs)
        assert duplicate.duplicate is True
        assert duplicate.message.id == first.message.id
    with pytest.raises(CustomerMessageConflict), factory.begin() as session:
        agent.handle_message(
            session,
            conversation_id,
            **{**kwargs, "content": "Changed content"},
        )
    assert len(llm.requests) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(CustomerMessage)) == 1
        assert session.scalar(select(func.count()).select_from(Approval)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "CUSTOMER_MESSAGE_RECEIVED")
            )
            == 1
        )
