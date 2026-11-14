"""Application configuration.

Settings are read from environment variables and, if present, the repository-root
``.env`` file. Real environment variables take precedence over ``.env`` values.
Secret values are held as ``SecretStr`` so they are masked in ``repr``/logs.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NIYAMLENS_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # `.env` also holds Docker Compose variables that are not app settings.
        extra="ignore",
    )

    app_name: str = "NiyamLens"
    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Read from the unprefixed DATABASE_URL variable. No default: there is no
    # safe password-free default, and the app must not silently pick a database.
    database_url: SecretStr | None = Field(default=None, validation_alias="DATABASE_URL")


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
