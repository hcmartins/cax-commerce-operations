import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

EXPECTED_TABLES = {
    "agent_runs",
    "approvals",
    "audit_events",
    "customer_conversations",
    "customer_messages",
    "domain_events",
    "event_handler_receipts",
    "inventory_items",
    "inventory_movements",
    "listing_drafts",
    "marketplace_listings",
    "order_items",
    "orders",
    "pricing_decisions",
    "procurement_requests",
    "products",
    "purchase_orders",
    "refunds",
    "reorder_recommendations",
    "returns",
    "supplier_quotes",
    "suppliers",
    "workflow_runs",
}


def test_migration_history_has_one_head() -> None:
    project_root = Path(__file__).parents[2]
    config = Config(project_root / "alembic.ini")
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_current_head() == "c6fa3e09b842"


def test_migrations_upgrade_and_downgrade_from_scratch(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[2]
    database_path = tmp_path / "migrations.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    environment = os.environ | {"COMMERCE_DATABASE_URL": database_url}

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
    engine.dispose()

    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgrade.returncode == 0, downgrade.stderr

    engine = create_engine(database_url)
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
