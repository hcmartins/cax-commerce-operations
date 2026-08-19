import uuid
from collections.abc import Collection, Sequence
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from commerce_operations.events.types import EventEnvelope, EventPayload, EventType, decode_payload
from commerce_operations.persistence.enums import EventStatus
from commerce_operations.persistence.models import DomainEvent


class EventConflictError(RuntimeError):
    pass


class EventPublisher(Protocol):
    """Port that can later be implemented by an external message broker."""

    def publish(self, session: Session, event: EventEnvelope) -> DomainEvent: ...


class DatabaseEventStore:
    """PostgreSQL-backed transactional outbox for the modular monolith."""

    def publish(self, session: Session, event: EventEnvelope) -> DomainEvent:
        existing = session.scalar(
            select(DomainEvent).where(DomainEvent.idempotency_key == event.idempotency_key)
        )
        payload = event.data.model_dump(mode="json")
        if existing is not None:
            if existing.event_type != event.event_type.value or existing.payload != payload:
                raise EventConflictError(
                    f"Idempotency key {event.idempotency_key!r} was reused for another event"
                )
            return existing

        record = DomainEvent(
            id=event.event_id,
            created_at=event.occurred_at,
            updated_at=event.occurred_at,
            event_type=event.event_type.value,
            event_version=event.event_version,
            producer=event.producer,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            aggregate_version=event.aggregate_version,
            correlation_id=event.correlation_id,
            workflow_id=event.workflow_id,
            causation_id=event.causation_id,
            idempotency_key=event.idempotency_key,
            payload=payload,
            publication_status=EventStatus.PENDING,
        )
        session.add(record)
        session.flush()
        return record

    def get(self, session: Session, event_id: uuid.UUID) -> DomainEvent | None:
        return session.get(DomainEvent, event_id)

    def pending(
        self,
        session: Session,
        *,
        limit: int = 100,
        event_types: Collection[EventType] | None = None,
    ) -> Sequence[DomainEvent]:
        statement = (
            select(DomainEvent)
            .where(DomainEvent.publication_status.in_([EventStatus.PENDING, EventStatus.FAILED]))
            .order_by(DomainEvent.created_at)
            .limit(limit)
        )
        if event_types is not None:
            statement = statement.where(
                DomainEvent.event_type.in_([event_type.value for event_type in event_types])
            )
        return session.scalars(statement).all()

    def to_envelope(self, record: DomainEvent) -> EventEnvelope[EventPayload]:
        event_type = EventType(record.event_type)
        return EventEnvelope[EventPayload](
            event_id=record.id,
            event_type=event_type,
            event_version=record.event_version,
            occurred_at=record.created_at,
            producer=record.producer,
            aggregate_type=record.aggregate_type,
            aggregate_id=record.aggregate_id,
            aggregate_version=record.aggregate_version,
            correlation_id=record.correlation_id,
            workflow_id=record.workflow_id,
            causation_id=record.causation_id,
            idempotency_key=record.idempotency_key,
            data=decode_payload(event_type, record.payload),
        )
