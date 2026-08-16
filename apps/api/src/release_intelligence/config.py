from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Deployment configuration; every secret is supplied outside source control."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ARI_",
        extra="ignore",
    )

    database_url: SecretStr
    credential_encryption_key: SecretStr
    github_app_id: Annotated[str, Field(min_length=1)]
    github_private_key_pem: SecretStr
    github_client_id: Annotated[str, Field(min_length=1)]
    github_client_secret: SecretStr
    session_ttl_seconds: Annotated[int, Field(gt=0, le=7 * 24 * 60 * 60)] = 8 * 60 * 60
    oauth_state_ttl_seconds: Annotated[int, Field(gt=0, le=60 * 60)] = 10 * 60
    openai_api_key: SecretStr | None = None
    openai_model: Annotated[str, Field(min_length=1, max_length=200)] = "gpt-5.6"
    openai_input_cost_per_million: (
        Annotated[
            Decimal,
            Field(ge=0, le=Decimal(1_000_000), allow_inf_nan=False, decimal_places=6),
        ]
        | None
    ) = None
    openai_output_cost_per_million: (
        Annotated[
            Decimal,
            Field(ge=0, le=Decimal(1_000_000), allow_inf_nan=False, decimal_places=6),
        ]
        | None
    ) = None

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("ARI_DATABASE_URL must use PostgreSQL with asyncpg")
        return value

    @field_validator("credential_encryption_key")
    @classmethod
    def require_fernet_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode())
        except (TypeError, ValueError) as error:
            raise ValueError(
                "ARI_CREDENTIAL_ENCRYPTION_KEY must be a Fernet key"
            ) from error
        return value

    @field_validator("github_private_key_pem")
    @classmethod
    def require_rsa_private_key(cls, value: SecretStr) -> SecretStr:
        try:
            key = serialization.load_pem_private_key(
                value.get_secret_value().encode(), password=None
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                "ARI_GITHUB_PRIVATE_KEY_PEM must be a private key"
            ) from error
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(  # noqa: TRY004 - Pydantic validators require ValueError
                "ARI_GITHUB_PRIVATE_KEY_PEM must be an RSA private key"
            )
        return value

    @field_validator("github_client_secret")
    @classmethod
    def require_non_empty_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("authentication secrets must not be empty")
        return value

    @field_validator("openai_api_key")
    @classmethod
    def require_non_empty_optional_secret(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None and not value.get_secret_value():
            raise ValueError("AI provider secret must not be empty")
        return value

    @model_validator(mode="after")
    def require_ai_prices_when_enabled(self) -> AppSettings:
        prices = (
            self.openai_input_cost_per_million,
            self.openai_output_cost_per_million,
        )
        if self.openai_api_key is not None and any(price is None for price in prices):
            raise ValueError("both AI token prices are required when AI is enabled")
        if self.openai_api_key is None and any(price is not None for price in prices):
            raise ValueError("AI token prices require an AI provider key")
        return self
