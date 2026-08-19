from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="COMMERCE_",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    app_name: str = "Commerce Operations API"
    app_version: str = "0.1.0"
    environment: Literal["local", "test", "staging", "production"] = "local"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: str = "postgresql+psycopg://commerce:commerce@localhost:5432/commerce_operations"
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=5, ge=0)
    database_connect_timeout_seconds: int = Field(default=5, ge=1)
    database_startup_timeout_seconds: int = Field(default=60, ge=1)
    cors_origins: list[str] = Field(default_factory=list)
    api_auth_enabled: bool = False
    api_keys: dict[str, SecretStr] = Field(default_factory=dict)
    api_roles: dict[str, list[str]] = Field(default_factory=dict)
    rate_limit_requests: int = Field(default=120, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    metrics_enabled: bool = True
    error_reporting_enabled: bool = False
    ai_monthly_spending_limit: float | None = Field(default=None, ge=0)
    workflow_spending_limit: float | None = Field(default=None, ge=0)
    spending_currency: str = Field(default="USD", min_length=3, max_length=3)
    dashboard_access_key: SecretStr | None = None
    dashboard_currency: str = Field(default="GBP", min_length=3, max_length=3)
    demo_mode: bool = False
    significant_price_change_percent: float = Field(default=10.0, gt=0)
    pricing_target_margin: float = Field(default=0.30, ge=0, lt=1)
    pricing_minimum_margin: float = Field(default=0.15, ge=0, lt=1)
    pricing_minimum_price: float = Field(default=0, ge=0)
    pricing_maximum_price: float | None = Field(default=None, gt=0)
    pricing_rounding_increment: float = Field(default=0.01, gt=0)
    refund_approval_threshold: float = Field(default=50.0, ge=0)
    default_approval_expiry_hours: int = Field(default=72, gt=0)
    workflow_max_attempts: int = Field(default=3, ge=1)
    workflow_timeout_seconds: int = Field(default=300, ge=1)
    workflow_retry_delay_seconds: int = Field(default=30, ge=1)
    worker_poll_interval_seconds: float = Field(default=2.0, gt=0)
    worker_batch_size: int = Field(default=100, ge=1, le=1000)
    worker_heartbeat_path: str = "/tmp/commerce-worker-heartbeat"
    worker_heartbeat_timeout_seconds: int = Field(default=30, ge=5)
    reorder_target_multiplier: int = Field(default=2, ge=1)
    marketplace_http_timeout_seconds: float = Field(default=30.0, gt=0)
    enabled_marketplaces: list[str] = Field(default_factory=list)
    ebay_access_token: SecretStr | None = None
    ebay_account_id: str | None = None
    ebay_endpoint: str = "https://api.ebay.com"
    ebay_marketplace_id: str = "EBAY_GB"
    ebay_merchant_location_key: str | None = None
    ebay_category_id: str | None = None
    ebay_fulfillment_policy_id: str | None = None
    ebay_payment_policy_id: str | None = None
    ebay_return_policy_id: str | None = None
    amazon_access_token: SecretStr | None = None
    amazon_seller_id: str | None = None
    amazon_marketplace_id: str | None = None
    amazon_endpoint: str = "https://sellingpartnerapi-eu.amazon.com"
    facebook_access_token: SecretStr | None = None
    facebook_catalog_id: str | None = None
    facebook_commerce_account_id: str | None = None
    facebook_endpoint: str = "https://graph.facebook.com"
    facebook_api_version: str = "v23.0"
    tiktok_access_token: SecretStr | None = None
    tiktok_app_key: str | None = None
    tiktok_app_secret: SecretStr | None = None
    tiktok_shop_cipher: SecretStr | None = None
    tiktok_endpoint: str = "https://open-api.tiktokglobalshop.com"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
