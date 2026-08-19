import uuid
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from commerce_operations.dashboard.data import overview, product_history, product_options
from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import ProductStatus
from commerce_operations.persistence.models import InventoryItem, Product


def test_dashboard_kpis_and_product_history_are_safe_when_database_is_sparse(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'dashboard.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        product = Product(
            source_system="intelligence",
            external_product_id="dashboard-product",
            source_workflow_run_id="source-run",
            source_recommendation_id="recommendation",
            source_payload_hash="0" * 64,
            name="Dashboard bottle",
            status=ProductStatus.APPROVED,
        )
        session.add(product)
        session.flush()
        session.add(
            InventoryItem(
                product_id=product.id,
                sku="DASH-1",
                storage_location="warehouse-a",
                quantity_on_hand=10,
                reserved_quantity=2,
                cost_basis=Decimal("4.50"),
                currency="GBP",
                low_stock_threshold=3,
            )
        )
        session.flush()

        metrics = overview(session)
        options = product_options(session)
        history = product_history(session, product.id)

        assert metrics["stock_value"] == Decimal("45.00")
        assert metrics["low_stock_products"] == 0
        assert metrics["orders"] == 0
        assert options == [("Dashboard bottle · dashboard-product", product.id)]
        assert history[0]["stage"] == "Product"
        assert history[0]["event"] == "Approved"
        assert product_history(session, uuid.uuid4()) == []
    engine.dispose()
