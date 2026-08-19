from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from commerce_operations.persistence import Base
from commerce_operations.persistence.enums import ProductStatus, SupplierStatus
from commerce_operations.persistence.models import Product, Supplier, SupplierQuote
from commerce_operations.persistence.repositories import SQLAlchemyRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session
    engine.dispose()


def product() -> Product:
    return Product(
        source_system="commerce-intelligence",
        external_product_id="product-123",
        source_workflow_run_id="workflow-123",
        source_recommendation_id="recommendation-123",
        source_payload_hash="test-payload-hash",
        name="Test product",
        status=ProductStatus.APPROVED,
    )


def test_repository_add_get_list_and_delete(session: Session) -> None:
    repository = SQLAlchemyRepository(session, Product)
    entity = repository.add(product())
    session.commit()

    assert entity.id is not None
    assert entity.created_at is not None
    assert entity.updated_at is not None
    assert entity.version == 1
    assert repository.get(entity.id) is entity
    assert repository.list() == [entity]

    repository.delete(entity)
    session.commit()
    assert repository.get(entity.id) is None


def test_catalog_relationships_are_persisted(session: Session) -> None:
    product_record = product()
    supplier = Supplier(
        source_system="commerce-intelligence",
        external_supplier_id="supplier-123",
        name="Supplier",
        status=SupplierStatus.ACTIVE,
    )
    quote = SupplierQuote(
        product=product_record,
        supplier=supplier,
        external_quote_id="quote-123",
        currency="GBP",
        moq=10,
        quantity=20,
        unit_cost=Decimal("2.50"),
        shipping_cost=Decimal("10.00"),
        lead_time_days=14,
    )
    session.add(quote)
    session.commit()

    assert quote.product.name == "Test product"
    assert product_record.supplier_quotes == [quote]
    assert quote.supplier.name == "Supplier"


def test_unique_external_product_constraint(session: Session) -> None:
    session.add(product())
    session.commit()
    duplicate = product()
    duplicate.source_workflow_run_id = "another-run"
    session.add(duplicate)

    with pytest.raises(IntegrityError):
        session.commit()


def test_optimistic_version_increments_on_update(session: Session) -> None:
    entity = product()
    session.add(entity)
    session.commit()
    entity.name = "Updated product"
    session.commit()

    assert entity.version == 2
    assert entity.updated_at <= datetime.now(UTC).replace(tzinfo=None)
