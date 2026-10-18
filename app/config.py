from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REFUND_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./refunds.db"
    # SQLite/dev only. In Azure SQL the schema (incl. ledger tables) comes from infra/sql.
    auto_create_schema: bool = True
    # Azure SQL: client ID of the user-assigned managed identity used to fetch Entra access tokens.
    sql_managed_identity_client_id: str | None = None

    auth_mode: Literal["entra", "dev"] = "dev"
    entra_tenant_id: str | None = None
    entra_api_client_id: str | None = None

    # Defaults; Refund.Admin can override at runtime (stored in app_settings, audited).
    # Refunds strictly above the threshold need two distinct checkers.
    dual_approval_threshold: Decimal = Decimal("50.00")
    max_refund_amount: Decimal = Decimal("50000.00")
    seed_demo_data: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.environment == "production" and self.auth_mode != "entra":
            raise ValueError("auth_mode must be 'entra' in production")
        if self.auth_mode == "entra" and not (self.entra_tenant_id and self.entra_api_client_id):
            raise ValueError("entra_tenant_id and entra_api_client_id are required for entra auth")
        if self.seed_demo_data and self.auth_mode != "dev":
            raise ValueError("seed_demo_data is only allowed with dev auth")
        if self.dual_approval_threshold <= 0 or self.max_refund_amount <= 0:
            raise ValueError("thresholds must be positive")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
