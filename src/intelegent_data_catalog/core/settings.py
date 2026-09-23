"""Environment-backed application settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Settings loaded from the process environment."""

    model_config = SettingsConfigDict(env_prefix="CATALOG_", extra="ignore")

    app_name: str = "intelegent-data-catalog"
    debug: bool = False
