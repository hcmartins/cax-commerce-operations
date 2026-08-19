import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from commerce_operations.persistence.database import session_scope


def test_session_scope_commits() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (value INTEGER NOT NULL)"))
    factory = sessionmaker(bind=engine)

    with session_scope(factory) as session:
        session.execute(text("INSERT INTO sample (value) VALUES (1)"))

    with Session(engine) as session:
        assert session.scalar(text("SELECT COUNT(*) FROM sample")) == 1


def test_session_scope_rolls_back() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE sample (value INTEGER NOT NULL)"))
    factory = sessionmaker(bind=engine)

    with pytest.raises(RuntimeError), session_scope(factory) as session:
        session.execute(text("INSERT INTO sample (value) VALUES (1)"))
        raise RuntimeError("stop")

    with Session(engine) as session:
        assert session.scalar(text("SELECT COUNT(*) FROM sample")) == 0
