"""Application settings loaded from settings.toml and the environment."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from intelegent_data_catalog.exceptions import SettingsError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DEFAULT_LOG_FORMAT: str = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
DEFAULT_DEBUG_LOG_FORMAT: str = (
    "%(asctime)s %(levelname)-8s [%(name)s] %(filename)s:%(lineno)d %(message)s"
)
SETTINGS_FILE_ENV: str = "CATALOG_SETTINGS_FILE"


class AppSection(BaseModel):
    """Process identity."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="intelegent-data-catalog", min_length=1)
    debug: bool = False


class LoggingSettings(BaseModel):
    """Logger level, format, and destinations."""

    model_config = ConfigDict(extra="forbid")

    level: LogLevel = "INFO"
    format: str = Field(default=DEFAULT_LOG_FORMAT, min_length=1)
    debug_format: str = Field(default=DEFAULT_DEBUG_LOG_FORMAT, min_length=1)
    date_format: str = Field(default="%Y-%m-%d %H:%M:%S", min_length=1)
    log_to_console: bool = True
    log_to_file: bool = False
    log_file: str = Field(default="logs/catalog.log", min_length=1)


class ScanSettings(BaseModel):
    """How often the context engine scans the db folder."""

    model_config = ConfigDict(extra="forbid")

    interval_seconds: float = Field(default=30.0, gt=0)
    db_dir: str = Field(default="db", min_length=1)


class ApiSettings(BaseModel):
    """Bind address for the catalog HTTP API."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8090, ge=1, le=65535)


class QuerySettings(BaseModel):
    """Limits and the Spark SQL endpoint for table samples and queries."""

    model_config = ConfigDict(extra="forbid")

    spark_url: str = Field(default="http://127.0.0.1:8091", min_length=1)
    sample_limit: int = Field(default=100, ge=1)
    max_sample_limit: int = Field(default=500, ge=1)
    max_query_rows: int = Field(default=500, ge=1)
    max_sql_chars: int = Field(default=8000, ge=1)
    query_timeout_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _sample_limit_fits(self) -> QuerySettings:
        if self.sample_limit > self.max_sample_limit:
            raise ValueError("sample_limit cannot exceed max_sample_limit")
        return self


class PolarisSettings(BaseModel):
    """Apache Polaris REST endpoint and local client credentials."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(default="http://localhost:8181", min_length=1)
    realm: str = Field(default="POLARIS", min_length=1)
    catalog: str = Field(default="catalog", min_length=1)
    client_id: str = Field(default="root", min_length=1)
    client_secret: str = Field(default="s3cr3t", min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)


class AppSettings(BaseSettings):
    """Settings from settings.toml, overridden by CATALOG_ environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CATALOG_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
        extra="forbid",
    )

    app: AppSection = Field(default_factory=AppSection)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    query: QuerySettings = Field(default_factory=QuerySettings)
    polaris: PolarisSettings = Field(default_factory=PolarisSettings)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Load init, environment, dotenv, then settings.toml."""
        toml_file: Path = resolve_settings_file()
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=toml_file),
            file_secret_settings,
        )

    def resolved_db_dir(self, project_root: Path) -> Path:
        """Return the db folder. Relative paths start at the project root."""
        configured: Path = Path(self.scan.db_dir)
        if configured.is_absolute():
            return configured
        return project_root / configured

    def resolved_log_file(self, project_root: Path) -> Path:
        """Return the log file path. Relative paths start at the project root."""
        configured: Path = Path(self.logging.log_file)
        if configured.is_absolute():
            return configured
        return project_root / configured


def find_project_root() -> Path:
    """Return the directory that contains pyproject.toml, or the current directory."""
    start: Path = Path.cwd().resolve()
    candidate: Path
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return start


def resolve_settings_file() -> Path:
    """Return the settings.toml path. CATALOG_SETTINGS_FILE overrides the default."""
    override: str | None = os.environ.get(SETTINGS_FILE_ENV)
    if override is not None and override.strip() != "":
        path: Path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise SettingsError(f"settings file not found: {path}")
        return path
    return find_project_root() / "settings.toml"


def resolve_project_root() -> Path:
    """Return the directory that contains the active settings file."""
    return resolve_settings_file().parent


def load_settings() -> AppSettings:
    """Load settings and translate file or validation failures."""
    try:
        return AppSettings()
    except SettingsError:
        raise
    except ValidationError as err:
        raise SettingsError(str(err)) from err
    except tomllib.TOMLDecodeError as err:
        raise SettingsError(f"invalid settings.toml: {err}") from err
