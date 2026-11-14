from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ENV_FILE, REPO_ROOT, Settings, get_settings

SETTINGS_ENV_VARS = (
    "DATABASE_URL",
    "NIYAMLENS_APP_NAME",
    "NIYAMLENS_ENVIRONMENT",
    "NIYAMLENS_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def test_defaults_without_env_or_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "NiyamLens"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.database_url is None


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIYAMLENS_ENVIRONMENT", "test")
    monkeypatch.setenv("NIYAMLENS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:pw@127.0.0.1:5433/niyamlens")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().endswith("@127.0.0.1:5433/niyamlens")


def test_values_are_read_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "NIYAMLENS_ENVIRONMENT=production\n"
        "DATABASE_URL=postgresql+psycopg://u:pw@127.0.0.1:5433/niyamlens\n"
        # Compose-only variables in the same file must not break settings loading.
        "NIYAMLENS_DB_PASSWORD=not-an-app-setting\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.environment == "production"
    assert settings.database_url is not None


def test_environment_variable_takes_precedence_over_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("NIYAMLENS_ENVIRONMENT=production\n", encoding="utf-8")
    monkeypatch.setenv("NIYAMLENS_ENVIRONMENT", "test")

    assert Settings(_env_file=env_file).environment == "test"


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIYAMLENS_ENVIRONMENT", "staging-typo")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_password_is_not_exposed_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:super-secret-pw@127.0.0.1:5433/db")
    settings = Settings(_env_file=None)

    assert "super-secret-pw" not in repr(settings)
    assert "super-secret-pw" not in str(settings)
    assert "super-secret-pw" not in settings.model_dump_json()


def test_env_file_points_to_repository_root() -> None:
    assert ENV_FILE == REPO_ROOT / ".env"
    assert (REPO_ROOT / "backend" / "pyproject.toml").is_file()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
