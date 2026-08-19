from pathlib import Path

from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from commerce_operations.persistence import Base


def test_dashboard_overview_starts_against_an_empty_database(tmp_path, monkeypatch):
    database = tmp_path / "dashboard-smoke.sqlite"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    engine.dispose()
    monkeypatch.setenv("COMMERCE_DATABASE_URL", f"sqlite+pysqlite:///{database}")
    monkeypatch.setenv("COMMERCE_ENVIRONMENT", "test")
    monkeypatch.delenv("COMMERCE_DASHBOARD_ACCESS_KEY", raising=False)

    app = AppTest.from_file(Path(__file__).parents[2] / "streamlit_app.py")
    app.run(timeout=30)

    assert not app.exception
    assert app.title[0].value.endswith("Overview")
    assert len(app.metric) == 10
    assert any("first approved product" in message.value for message in app.info)
