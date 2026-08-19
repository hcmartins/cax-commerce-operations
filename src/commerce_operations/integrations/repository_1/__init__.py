"""Versioned anti-corruption boundary for Commerce Intelligence MVP."""

from commerce_operations.integrations.repository_1.contracts import (
    ApprovedProductRequestV1,
    ApprovedProductResponseV1,
)
from commerce_operations.integrations.repository_1.service import (
    ApprovedProductIngestionService,
)

__all__ = [
    "ApprovedProductIngestionService",
    "ApprovedProductRequestV1",
    "ApprovedProductResponseV1",
]
