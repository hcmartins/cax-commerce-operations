from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.config import Settings, get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_database_engine(settings)


def create_database_engine(settings: Settings) -> Engine:
    url = make_url(settings.database_url)
    options: dict = {"pool_pre_ping": True}
    if url.get_backend_name() == "postgresql":
        options.update(
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
    return create_engine(url, **options)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    session_factory = create_session_factory(get_engine())
    with session_factory() as session:
        yield session


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit a unit of work or roll it back before closing its session."""

    with session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def database_is_ready() -> bool:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
