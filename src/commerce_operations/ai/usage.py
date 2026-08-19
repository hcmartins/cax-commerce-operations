from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from commerce_operations.ai.provider import LLMUsage
from commerce_operations.persistence.models import AgentRun, WorkflowRun


class SpendingLimitExceeded(RuntimeError):
    pass


class UsageAccounting:
    """Enforces budgets and accounts provider-reported usage without storing prompts."""

    def __init__(
        self,
        *,
        monthly_limit: Decimal | None = None,
        workflow_limit: Decimal | None = None,
        currency: str = "USD",
    ) -> None:
        self.monthly_limit = monthly_limit
        self.workflow_limit = workflow_limit
        self.currency = currency.upper()

    @classmethod
    def from_settings(cls, settings) -> "UsageAccounting":
        return cls(
            monthly_limit=(
                Decimal(str(settings.ai_monthly_spending_limit))
                if settings.ai_monthly_spending_limit is not None
                else None
            ),
            workflow_limit=(
                Decimal(str(settings.workflow_spending_limit))
                if settings.workflow_spending_limit is not None
                else None
            ),
            currency=settings.spending_currency,
        )

    def authorize(self, session: Session, workflow: WorkflowRun | None = None) -> None:
        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly = session.scalar(
            select(func.coalesce(func.sum(AgentRun.cost_amount), 0)).where(
                AgentRun.created_at >= month_start,
                AgentRun.cost_currency == self.currency,
            )
        )
        if self.monthly_limit is not None and Decimal(monthly) >= self.monthly_limit:
            raise SpendingLimitExceeded("Monthly AI spending limit reached")
        if (
            workflow is not None
            and self.workflow_limit is not None
            and workflow.cost_currency == self.currency
            and workflow.cost_amount >= self.workflow_limit
        ):
            raise SpendingLimitExceeded("Workflow AI spending limit reached")

    def record(self, workflow: WorkflowRun | None, agent_run: AgentRun, usage: LLMUsage) -> None:
        if usage.cost_amount < 0 or usage.input_tokens < 0 or usage.output_tokens < 0:
            raise ValueError("AI usage cannot be negative")
        agent_run.input_tokens = usage.input_tokens
        agent_run.output_tokens = usage.output_tokens
        agent_run.cost_amount = usage.cost_amount
        agent_run.cost_currency = usage.cost_currency
        if usage.cost_currency and workflow is not None:
            currency = usage.cost_currency.upper()
            if workflow.cost_currency not in {None, currency}:
                raise ValueError("A workflow cannot aggregate costs in multiple currencies")
            workflow.cost_currency = currency
            workflow.cost_amount += usage.cost_amount
