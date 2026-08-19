import argparse
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.application.orchestration import AutonomousOrchestrator
from commerce_operations.config import Settings
from commerce_operations.events import DatabaseEventStore, EventHandlerRegistry, EventProcessor
from commerce_operations.events.processor import EventProcessingError
from commerce_operations.events.types import EventType
from commerce_operations.observability.logging import configure_logging
from commerce_operations.persistence.database import (
    create_database_engine,
    create_session_factory,
    database_is_ready,
)

logger = logging.getLogger(__name__)

# These workflows require no external AI or marketplace client. Events requiring an
# unconfigured specialist remain pending instead of being acknowledged or guessed through.
CORE_EVENT_TYPES = {
    EventType.PRODUCT_APPROVED,
    EventType.PURCHASE_APPROVED,
    EventType.ORDER_RECEIVED,
    EventType.LOW_STOCK,
    EventType.ORDER_RETURNED,
}


class Worker:
    def __init__(self, settings: Settings, factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self.factory = factory
        self.registry = EventHandlerRegistry()
        self.orchestrator = AutonomousOrchestrator(settings=settings)
        self.orchestrator.register(self.registry, CORE_EVENT_TYPES)
        self.processor = EventProcessor(factory, self.registry)
        self.store = DatabaseEventStore()
        self.running = True

    def run_once(self) -> int:
        processed = 0
        with self.factory() as session:
            candidates = self.store.pending(
                session,
                limit=self.settings.worker_batch_size,
                event_types=CORE_EVENT_TYPES,
            )
            event_ids = [record.id for record in candidates]
        for event_id in event_ids:
            try:
                self.processor.process(event_id)
                processed += 1
            except EventProcessingError:
                logger.exception("worker_event_processing_failed event_id=%s", event_id)
        with self.factory.begin() as session:
            retried = self.orchestrator.engine.retry_due(
                session, limit=self.settings.worker_batch_size
            )
        self._heartbeat()
        logger.info("worker_cycle_complete events=%s workflow_retries=%s", processed, len(retried))
        return processed + len(retried)

    def run(self) -> None:
        logger.info("worker_started")
        while self.running:
            self.run_once()
            time.sleep(self.settings.worker_poll_interval_seconds)
        logger.info("worker_stopped")

    def stop(self, *_args) -> None:
        self.running = False

    def _heartbeat(self) -> None:
        Path(self.settings.worker_heartbeat_path).write_text(
            datetime.now(UTC).isoformat(), encoding="utf-8"
        )


def healthcheck(settings: Settings) -> int:
    database_is_ready()
    heartbeat = Path(settings.worker_heartbeat_path)
    if not heartbeat.exists():
        return 1
    age = time.time() - heartbeat.stat().st_mtime
    return 0 if age <= settings.worker_heartbeat_timeout_seconds else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Commerce Operations event worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    if args.healthcheck:
        return healthcheck(settings)
    engine = create_database_engine(settings)
    worker = Worker(settings, create_session_factory(engine))
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    if args.once:
        worker.run_once()
    else:
        worker.run()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
