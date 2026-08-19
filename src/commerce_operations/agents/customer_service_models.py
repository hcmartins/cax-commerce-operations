from pydantic import BaseModel, ConfigDict, Field

from commerce_operations.persistence.enums import CustomerIntent, CustomerServiceDecision


class CustomerServiceAIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: CustomerIntent
    decision: CustomerServiceDecision
    risk_level: str = Field(min_length=1, max_length=50)
    generated_response: str | None = Field(default=None, max_length=5000)
    rationale: str = Field(min_length=1, max_length=1000)
