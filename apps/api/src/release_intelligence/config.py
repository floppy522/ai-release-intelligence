from __future__ import annotations

from pydantic import SecretStr
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
    github_app_id: str
    github_private_key_pem: SecretStr
    github_client_id: str
    github_client_secret: SecretStr
    session_ttl_seconds: int = 8 * 60 * 60
    oauth_state_ttl_seconds: int = 10 * 60
