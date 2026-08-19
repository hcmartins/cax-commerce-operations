import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.agents.customer_service_models import CustomerServiceAIResponse
from commerce_operations.ai import LLMRequest, StructuredLLM, UsageAccounting
from commerce_operations.approvals.engine import ApprovalActionRegistry, ApprovalEngine
from commerce_operations.approvals.policy import ApprovalActionType
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.events.types import (
    CustomerMessageReceivedPayload,
    EventEnvelope,
    EventPayload,
    create_event,
)
from commerce_operations.persistence.enums import (
    ApprovalStatus,
    ConversationStatus,
    CustomerIntent,
    CustomerServiceDecision,
    MessageDirection,
    MessageStatus,
    RunStatus,
)
from commerce_operations.persistence.models import (
    AgentRun,
    Approval,
    AuditEvent,
    CustomerConversation,
    CustomerMessage,
)


class ConversationNotFoundError(LookupError):
    pass


class CustomerMessageConflict(RuntimeError):
    pass


class CustomerResponseApprovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class CustomerServiceResult:
    message: CustomerMessage
    duplicate: bool


class CustomerRiskScreen:
    rules = {
        "legal_threat": re.compile(
            r"\b(lawyer|solicitor|sue|court|legal action|regulator|trading standards)\b",
            re.IGNORECASE,
        ),
        "dispute": re.compile(r"\b(chargeback|payment dispute|open a dispute)\b", re.IGNORECASE),
        "suspected_fraud": re.compile(
            r"\b(fraud|fraudulent|scam|stolen card|unauthori[sz]ed)\b", re.IGNORECASE
        ),
        "abusive_or_threatening": re.compile(
            r"\b(kill|hurt you|threat|idiot|moron|fuck|bastard)\b", re.IGNORECASE
        ),
        "unusual_refund": re.compile(
            r"\b(refund.*(different (card|account)|gift card|crypto)|multiple refunds)\b",
            re.IGNORECASE,
        ),
    }

    def evaluate(self, content: str, conversation: CustomerConversation) -> list[str]:
        reasons = [name for name, pattern in self.rules.items() if pattern.search(content)]
        if conversation.risk_level in {"high", "critical"}:
            reasons.append("existing_high_risk_conversation")
        return sorted(set(reasons))


class CustomerServiceAgent:
    safe_auto_intents = {
        CustomerIntent.PRODUCT_ENQUIRY,
        CustomerIntent.ORDER_STATUS,
        CustomerIntent.DELIVERY_QUESTION,
        CustomerIntent.COMMON_MARKETPLACE_MESSAGE,
    }
    approval_intents = {
        CustomerIntent.RETURN_REQUEST,
        CustomerIntent.REFUND_ENQUIRY,
        CustomerIntent.COMPLAINT,
    }

    def __init__(
        self,
        llm: StructuredLLM,
        approval_engine: ApprovalEngine,
        *,
        prompt_version: str = "customer-service-v1",
        risk_screen: CustomerRiskScreen | None = None,
        event_store: DatabaseEventStore | None = None,
        usage_accounting: UsageAccounting | None = None,
    ) -> None:
        self.llm = llm
        self.approval_engine = approval_engine
        self.prompt_version = prompt_version
        self.risk_screen = risk_screen or CustomerRiskScreen()
        self.event_store = event_store or DatabaseEventStore()
        self.usage_accounting = usage_accounting or UsageAccounting()

    def handle_message(
        self,
        session: Session,
        conversation_id: uuid.UUID,
        *,
        external_message_id: str,
        channel: str,
        content: str,
    ) -> CustomerServiceResult:
        existing = session.scalar(
            select(CustomerMessage).where(
                CustomerMessage.conversation_id == conversation_id,
                CustomerMessage.external_message_id == external_message_id,
            )
        )
        if existing is not None:
            if existing.content != content or existing.channel != channel:
                raise CustomerMessageConflict(
                    "External message ID was reused with different content"
                )
            return CustomerServiceResult(existing, True)
        conversation = session.scalar(
            select(CustomerConversation)
            .where(CustomerConversation.id == conversation_id)
            .with_for_update()
        )
        if conversation is None:
            raise ConversationNotFoundError(str(conversation_id))
        message = CustomerMessage(
            conversation_id=conversation.id,
            external_message_id=external_message_id,
            direction=MessageDirection.INBOUND,
            channel=channel,
            content=content,
            author_type="customer",
            status=MessageStatus.RECEIVED,
            risk_reasons=[],
        )
        session.add(message)
        session.flush()
        self.event_store.publish(
            session,
            create_event(
                CustomerMessageReceivedPayload(
                    conversation_id=conversation.id,
                    message_id=message.id,
                    channel=channel,
                ),
                aggregate_type="customer_conversation",
                aggregate_id=conversation.id,
                aggregate_version=conversation.version,
                correlation_id=conversation.id,
                idempotency_key=f"customer-message-received:{message.id}",
            ),
        )

        risks = self.risk_screen.evaluate(content, conversation)
        if risks:
            self._escalate(conversation, message, risks, intent=CustomerIntent.UNKNOWN)
        else:
            self._classify_with_ai(session, conversation, message)
        self._audit(session, conversation, message)
        session.flush()
        return CustomerServiceResult(message, False)

    def process_received_event(
        self,
        event: EventEnvelope[EventPayload],
        session: Session,
    ) -> CustomerMessage:
        payload = CustomerMessageReceivedPayload.model_validate(event.data)
        message = session.scalar(
            select(CustomerMessage)
            .where(CustomerMessage.id == payload.message_id)
            .with_for_update()
        )
        if message is None or message.conversation_id != payload.conversation_id:
            raise ConversationNotFoundError("Customer message event references missing data")
        if message.classification is not None:
            return message
        conversation = message.conversation
        risks = self.risk_screen.evaluate(message.content, conversation)
        if risks:
            self._escalate(conversation, message, risks, intent=CustomerIntent.UNKNOWN)
        else:
            self._classify_with_ai(session, conversation, message)
        self._audit(session, conversation, message)
        return message

    def _classify_with_ai(
        self,
        session: Session,
        conversation: CustomerConversation,
        message: CustomerMessage,
    ) -> None:
        agent_run = AgentRun(
            agent_type="customer_service",
            status=RunStatus.RUNNING,
            input_reference=str(message.id),
            prompt_version=self.prompt_version,
        )
        session.add(agent_run)
        session.flush()
        try:
            self.usage_accounting.authorize(session)
            result = self.llm.generate(
                LLMRequest(
                    task="classify_and_draft_customer_service_response",
                    prompt_version=self.prompt_version,
                    context=self._minimum_context(conversation, message),
                    constraints={
                        "allowed_intents": [intent.value for intent in CustomerIntent],
                        "decisions": [decision.value for decision in CustomerServiceDecision],
                        "never_promise_refunds_or_policy_exceptions": True,
                        "maximum_response_characters": 5000,
                    },
                ),
                CustomerServiceAIResponse,
            )
            output = CustomerServiceAIResponse.model_validate(result.output)
        except Exception:
            agent_run.status = RunStatus.FAILED
            agent_run.error = {"type": "ai_processing_failure"}
            self._escalate(
                conversation,
                message,
                ["ai_processing_failure"],
                intent=CustomerIntent.UNKNOWN,
            )
            return
        agent_run.status = RunStatus.SUCCEEDED
        agent_run.provider = result.provider
        agent_run.model = result.model
        self.usage_accounting.record(None, agent_run, result.usage)
        message.intent = output.intent
        message.generated_response = output.generated_response
        message.structured_ai_response = output.model_dump(mode="json")
        message.prompt_version = self.prompt_version
        message.ai_provider = result.provider
        message.ai_model = result.model
        message.input_tokens = result.usage.input_tokens
        message.output_tokens = result.usage.output_tokens
        message.ai_cost_amount = result.usage.cost_amount
        message.ai_cost_currency = result.usage.cost_currency

        if output.intent is CustomerIntent.UNKNOWN:
            self._escalate(conversation, message, ["outside_defined_policy"], output.intent)
        elif output.risk_level.lower() in {"high", "critical"}:
            self._escalate(conversation, message, ["ai_high_risk"], output.intent)
        elif output.decision is CustomerServiceDecision.HUMAN_ESCALATION:
            self._escalate(conversation, message, ["ai_requested_escalation"], output.intent)
        elif not output.generated_response:
            self._escalate(conversation, message, ["missing_generated_response"], output.intent)
        elif (
            output.decision is CustomerServiceDecision.DRAFT_FOR_APPROVAL
            or output.intent in self.approval_intents
        ):
            self._draft_for_approval(session, conversation, message)
        elif output.intent in self.safe_auto_intents:
            self._auto_respond(conversation, message)
        else:
            self._escalate(conversation, message, ["outside_defined_policy"], output.intent)

    def _draft_for_approval(
        self,
        session: Session,
        conversation: CustomerConversation,
        message: CustomerMessage,
    ) -> None:
        message.classification = CustomerServiceDecision.DRAFT_FOR_APPROVAL
        message.risk_level = "medium"
        message.status = MessageStatus.DRAFT
        conversation.classification = CustomerServiceDecision.DRAFT_FOR_APPROVAL.value
        conversation.risk_level = "medium"
        conversation.status = ConversationStatus.AWAITING_APPROVAL
        approval = self.approval_engine.create_request(
            session,
            action_type=ApprovalActionType.CUSTOMER_RESPONSE.value,
            resource_type="customer_message",
            resource_id=message.id,
            requested_action={"final_response": message.generated_response},
            reason="Customer response requires human review",
            requester="customer-service-agent",
            risk_level="customer_communication",
            rule_name="customer_response_review",
            rule_version=1,
        )
        message.approval_id = approval.id

    @staticmethod
    def _auto_respond(conversation: CustomerConversation, message: CustomerMessage) -> None:
        message.classification = CustomerServiceDecision.AUTO_RESPOND
        message.risk_level = "low"
        message.final_response = message.generated_response
        message.status = MessageStatus.APPROVED
        message.responded_at = datetime.now(UTC)
        conversation.classification = CustomerServiceDecision.AUTO_RESPOND.value
        conversation.risk_level = "low"
        conversation.status = ConversationStatus.OPEN

    @staticmethod
    def _escalate(
        conversation: CustomerConversation,
        message: CustomerMessage,
        reasons: list[str],
        intent: CustomerIntent,
    ) -> None:
        message.intent = intent
        message.classification = CustomerServiceDecision.HUMAN_ESCALATION
        message.risk_level = "high"
        message.risk_reasons = reasons
        message.status = MessageStatus.RECEIVED
        conversation.classification = CustomerServiceDecision.HUMAN_ESCALATION.value
        conversation.risk_level = "high"
        conversation.status = ConversationStatus.ESCALATED
        conversation.assignee = "human-support-queue"

    @staticmethod
    def _minimum_context(conversation: CustomerConversation, message: CustomerMessage) -> dict:
        context: dict = {
            "message": {"content": message.content, "channel": message.channel},
            "conversation": {
                "marketplace": conversation.marketplace,
                "status": conversation.status.value,
                "existing_classification": conversation.classification,
                "risk_level": conversation.risk_level,
            },
        }
        if conversation.product is not None:
            context["product"] = {
                "name": conversation.product.name,
                "brand": conversation.product.brand,
                "attributes": {
                    key: conversation.product.attributes[key]
                    for key in sorted(conversation.product.attributes)[:20]
                },
            }
        if conversation.order is not None:
            context["order"] = {
                "external_order_id": conversation.order.external_order_id,
                "status": conversation.order.status.value,
                "ordered_at": conversation.order.ordered_at.isoformat(),
                "dispatched_at": (
                    conversation.order.dispatched_at.isoformat()
                    if conversation.order.dispatched_at
                    else None
                ),
                "delivered_at": (
                    conversation.order.delivered_at.isoformat()
                    if conversation.order.delivered_at
                    else None
                ),
                "items": [
                    {"sku": item.sku, "quantity": item.quantity}
                    for item in conversation.order.items
                ],
            }
        return context

    @staticmethod
    def _audit(
        session: Session,
        conversation: CustomerConversation,
        message: CustomerMessage,
    ) -> None:
        session.add(
            AuditEvent(
                actor_type="agent",
                actor_id="customer-service-agent",
                action="customer_message.classified",
                resource_type="customer_message",
                resource_id=message.id,
                after_state={
                    "intent": message.intent.value if message.intent else None,
                    "classification": (
                        message.classification.value if message.classification else None
                    ),
                    "risk_reasons": message.risk_reasons,
                },
                reason="Deterministic safety screening and structured AI classification",
                correlation_id=conversation.id,
            )
        )


class CustomerResponseApprovalService:
    @staticmethod
    def approve_response(approval: Approval, session: Session) -> None:
        if approval.resource_type != "customer_message":
            raise CustomerResponseApprovalError("Approval references the wrong resource")
        message = session.scalar(
            select(CustomerMessage)
            .where(CustomerMessage.id == approval.resource_id)
            .with_for_update()
        )
        if message is None:
            raise CustomerResponseApprovalError("Customer message was not found")
        if message.approval_id != approval.id or message.status is not MessageStatus.DRAFT:
            raise CustomerResponseApprovalError("Message is not awaiting this approval")
        if approval.status is not ApprovalStatus.APPROVED:
            # The approval engine sets approved before invoking this handler.
            raise CustomerResponseApprovalError("Customer response has not been approved")
        message.final_response = message.generated_response
        message.status = MessageStatus.APPROVED
        message.responded_at = datetime.now(UTC)
        message.conversation.status = ConversationStatus.OPEN


def register_customer_response_approval_handler(registry: ApprovalActionRegistry) -> None:
    registry.register(
        ApprovalActionType.CUSTOMER_RESPONSE.value,
        CustomerResponseApprovalService.approve_response,
    )
