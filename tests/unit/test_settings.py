from commerce_operations.config import Settings


def test_settings_have_safe_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_production_disables_developer_docs() -> None:
    settings = Settings(environment="production", _env_file=None)

    assert settings.is_production is True


def test_blank_optional_numeric_environment_values_use_defaults(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "COMMERCE_AI_MONTHLY_SPENDING_LIMIT=\n"
        "COMMERCE_WORKFLOW_SPENDING_LIMIT=\n"
        "COMMERCE_PRICING_MAXIMUM_PRICE=\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.ai_monthly_spending_limit is None
    assert settings.workflow_spending_limit is None
    assert settings.pricing_maximum_price is None
