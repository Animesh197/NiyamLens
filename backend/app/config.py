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

    # --- RBI source ingestion (read-only, allowlisted HTTP GET) ---
    # Root for downloaded raw files (data/raw) and run reports (data/processed).
    data_dir: Path = REPO_ROOT / "data"
    http_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    http_read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    # Retries apply only to transient failures (network errors, 429, 5xx).
    http_max_retries: int = Field(default=2, ge=0, le=5)
    http_retry_backoff_seconds: float = Field(default=2.0, ge=0, le=30)
    http_max_redirects: int = Field(default=5, ge=0, le=10)
    http_max_page_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    http_max_pdf_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    # Pause between sources so the RBI site is not hit in a burst.
    http_request_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    # Generic client identifier; contains no personal or contact details.
    http_user_agent: str = "NiyamLens-research-prototype/0.1 (read-only)"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
