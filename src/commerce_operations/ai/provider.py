from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

StructuredOutputT = TypeVar("StructuredOutputT", bound=BaseModel)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_amount: Decimal = Decimal("0")
    cost_currency: str | None = None


@dataclass(frozen=True)
class LLMRequest:
    task: str
    prompt_version: str
    context: dict[str, Any]
    constraints: dict[str, Any]


@dataclass(frozen=True)
class LLMResult[StructuredOutputT: BaseModel]:
    output: StructuredOutputT
    provider: str
    model: str
    usage: LLMUsage = LLMUsage()
    provider_metadata: dict[str, Any] | None = None


class StructuredLLM(Protocol):
    def generate(
        self,
        request: LLMRequest,
        response_model: type[StructuredOutputT],
    ) -> LLMResult[StructuredOutputT]: ...
