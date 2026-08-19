import uuid
from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from commerce_operations.persistence.base import Base


class SQLAlchemyRepository[ModelT: Base]:
    """Small data-access adapter; domain-specific queries can subclass it later."""

    def __init__(self, session: Session, model_type: type[ModelT]) -> None:
        self.session = session
        self.model_type = model_type

    def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return self.session.get(self.model_type, entity_id)

    def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        entity = self.get(entity_id)
        if entity is None:
            raise LookupError(f"{self.model_type.__name__} {entity_id} was not found")
        return entity

    def list(self, *, offset: int = 0, limit: int = 100) -> Sequence[ModelT]:
        statement: Select[tuple[ModelT]] = (
            (select(self.model_type).order_by(self.model_type.created_at, self.model_type.id))
            .offset(offset)
            .limit(limit)
        )
        return self.session.scalars(statement).all()

    def delete(self, entity: ModelT) -> None:
        self.session.delete(entity)
        self.session.flush()
