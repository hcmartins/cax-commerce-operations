import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.events.handlers import EventHandlerRegistry, RegisteredHandler
from commerce_operations.events.store import DatabaseEventStore
from commerce_operations.persistence.enums import EventStatus, HandlerReceiptStatus
from commerce_operations.persistence.models import DomainEvent, EventHandlerReceipt

logger = logging.getLogger(__name__)


class EventNotFoundError(LookupError):
    pass


class EventProcessingError(RuntimeError):
    def __init__(self, event_id: uuid.UUID, handler_name: str) -> None:
        super().__init__(f"Event {event_id} failed in handler {handler_name}")
        self.event_id = event_id
        self.handler_name = handler_name


@dataclass(frozen=True)
class ProcessingResult:
    event_id: uuid.UUID
    handlers_processed: int
    handlers_skipped: int


class EventProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: EventHandlerRegistry,
        store: DatabaseEventStore | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.store = store or DatabaseEventStore()

    def process(self, event_id: uuid.UUID) -> ProcessingResult:
        with self.session_factory() as session:
            record = session.get(DomainEvent, event_id)
            if record is None:
                raise EventNotFoundError(str(event_id))
            envelope = self.store.to_envelope(record)
            handlers = self.registry.handlers_for(envelope.event_type)

        processed = 0
        skipped = 0
        for handler in handlers:
            try:
                handled = self._process_handler(event_id, handler)
            except Exception as exc:
                self._record_failure(event_id, handler.name, exc)
                raise EventProcessingError(event_id, handler.name) from exc
            if handled:
                processed += 1
            else:
                skipped += 1

        self._mark_complete(event_id)
        return ProcessingResult(event_id, processed, skipped)

    def _process_handler(self, event_id: uuid.UUID, handler: RegisteredHandler) -> bool:
        with self.session_factory.begin() as session:
            record = session.scalar(
                select(DomainEvent).where(DomainEvent.id == event_id).with_for_update()
            )
            if record is None:
                raise EventNotFoundError(str(event_id))
            receipt = session.scalar(
                select(EventHandlerReceipt).where(
                    EventHandlerReceipt.event_id == event_id,
                    EventHandlerReceipt.handler_name == handler.name,
                )
            )
            if receipt is not None:
                return False

            session.add(
                EventHandlerReceipt(
                    event_id=event_id,
                    handler_name=handler.name,
                    status=HandlerReceiptStatus.COMPLETED,
                )
            )
            session.flush()
            handler.callback(self.store.to_envelope(record), session)
            return True

    def _record_failure(self, event_id: uuid.UUID, handler_name: str, exception: Exception) -> None:
        logger.exception(
            "event_handler_failed event_id=%s handler=%s",
            event_id,
            handler_name,
            exc_info=exception,
        )
        with self.session_factory.begin() as session:
            record = session.get(DomainEvent, event_id)
            if record is not None:
                record.processing_attempts += 1
                record.publication_status = EventStatus.FAILED
                record.last_error = f"{handler_name}: {type(exception).__name__}: {exception}"[
                    :4000
                ]

    def _mark_complete(self, event_id: uuid.UUID) -> None:
        with self.session_factory.begin() as session:
            record = session.get(DomainEvent, event_id)
            if record is None:
                raise EventNotFoundError(str(event_id))
            record.processing_attempts += 1
            record.publication_status = EventStatus.PUBLISHED
            record.last_error = None
