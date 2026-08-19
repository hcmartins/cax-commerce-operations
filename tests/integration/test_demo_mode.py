from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from commerce_operations.config import Settings
from commerce_operations.dashboard.data import (
    action_required,
    automation_activity,
    navigation_counts,
    overview,
    product_history,
    product_performance,
)
from commerce_operations.demo import DEMO_SOURCE, remove_demo_data, seed_demo_data
from commerce_operations.integrations.marketplaces.factory import build_marketplace_connectors
from commerce_operations.persistence import Base
from commerce_operations.persistence.models import Product


@pytest.fixture
def demo_session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'demo.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def demo_settings(**values) -> Settings:
    return Settings(demo_mode=True, environment="test", _env_file=None, **values)


def test_demo_seed_is_idempotent_and_populates_client_story(demo_session):
    assert seed_demo_data(demo_session, demo_settings()) == 9
    demo_session.commit()
    assert seed_demo_data(demo_session, demo_settings()) == 0

    metrics = overview(demo_session)
    assert metrics["stock_value"] > Decimal("0")
    assert metrics["revenue"] > Decimal("0")
    assert metrics["realised_profit"] > Decimal("0")
    assert metrics["low_stock_products"] >= 1
    assert metrics["failed_workflows"] == 1
    assert navigation_counts(demo_session)["pending_approvals"] == 2
    assert any(row["status"] == "FAILED" for row in automation_activity(demo_session))
    assert {row["destination"] for row in action_required(demo_session)} == {
        "Pending approvals",
        "Failures / exceptions",
    }

    product = demo_session.scalar(select(Product).where(Product.external_product_id == "DEMO-001"))
    history = product_history(demo_session, product.id)
    performance = product_performance(demo_session, product.id)
    assert {row["stage"] for row in history} >= {
        "Product",
        "Procurement",
        "Inventory",
        "Listing agent",
        "Published listing",
        "Sale",
    }
    assert performance["units_sold"] == 8
    assert performance["realised_roi"] > 0


def test_demo_remove_deletes_only_demo_records(demo_session):
    real = Product(
        source_system="intelligence",
        external_product_id="real-product",
        source_workflow_run_id="real-run",
        source_recommendation_id="real-rec",
        source_payload_hash="1" * 64,
        name="Preserved product",
    )
    demo_session.add(real)
    seed_demo_data(demo_session, demo_settings())
    demo_session.commit()

    assert remove_demo_data(demo_session, demo_settings()) == 9
    demo_session.commit()
    assert (
        demo_session.scalar(
            select(func.count()).select_from(Product).where(Product.source_system == DEMO_SOURCE)
        )
        == 0
    )
    assert demo_session.get(Product, real.id) is not None


def test_demo_commands_refuse_unsafe_environments(demo_session):
    with pytest.raises(RuntimeError, match="require COMMERCE_DEMO_MODE"):
        seed_demo_data(demo_session, Settings(environment="test", _env_file=None))
    with pytest.raises(RuntimeError, match="disabled in production"):
        seed_demo_data(
            demo_session, Settings(environment="production", demo_mode=True, _env_file=None)
        )


def test_demo_mode_never_constructs_live_marketplace_connectors():
    settings = demo_settings(
        enabled_marketplaces=["ebay"],
        ebay_access_token="would-be-secret",
        ebay_account_id="account",
    )
    registry = build_marketplace_connectors(settings)
    with pytest.raises(LookupError, match="not registered"):
        registry.get("ebay")
