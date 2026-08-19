import argparse
import logging
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from commerce_operations.config import Settings
from commerce_operations.observability.logging import configure_logging
from commerce_operations.persistence.database import create_database_engine

logger = logging.getLogger(__name__)


def wait_for_database(settings: Settings) -> None:
    deadline = time.monotonic() + settings.database_startup_timeout_seconds
    engine = create_database_engine(settings)
    try:
        while True:
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                logger.info("database_ready")
                return
            except SQLAlchemyError:
                if time.monotonic() >= deadline:
                    logger.exception("database_startup_timeout")
                    raise
                logger.warning("database_not_ready retrying=true")
                time.sleep(1)
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deployment lifecycle commands")
    parser.add_argument("command", choices=["wait-for-db"])
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    if args.command == "wait-for-db":
        wait_for_database(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
