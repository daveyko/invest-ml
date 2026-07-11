from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings loaded from environment variables or .env.

    Used by service-layer code that runs outside Dagster (scripts, tests).
    Dagster resources use EnvVar references and do not read from this class.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/invest_ml"
    sec_user_agent: str = "invest-ml contact@example.com"
    artifact_root: str = "var"
    dagster_home: str = ".dagster"

    @property
    def artifact_root_path(self) -> Path:
        return Path(self.artifact_root)
