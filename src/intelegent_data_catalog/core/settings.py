"""Application settings loaded from settings.toml and the environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)
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


class DatabaseSettings(BaseModel):
    """Application catalog database on the shared PostgreSQL server."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        default="postgresql+psycopg://catalog:catalog@127.0.0.1:5432/catalog",
        min_length=1,
    )
    auto_migrate: bool = False


class MarquezSettings(BaseModel):
    """Marquez API used for lineage projection. Disabled until configured."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    url: str = Field(default="http://127.0.0.1:5050", min_length=1)
    timeout_seconds: float = Field(default=10.0, gt=0)


class Neo4jSettings(BaseModel):
    """Neo4j projection. Disabled until configured. PostgreSQL stays authoritative."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    uri: str = Field(default="bolt://127.0.0.1:7687", min_length=1)
    user: str = Field(default="neo4j", min_length=1)
    password: str = Field(default="password", min_length=1)
    database: str = Field(default="neo4j", min_length=1)


class TrinoSettings(BaseModel):
    """Trino coordinator used for metadata discovery, not query execution."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8088, ge=1, le=65535)
    user: str = Field(default="catalog", min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)


LlmProvider = Literal["claude", "openai", "ollama"]
ModelKind = Literal["local", "gpt"]
ModelId = Literal["llama3.2", "qwen3:4b", "gpt-4.1", "gpt-4.1-mini", "gpt-6-astra"]
ReasoningEffort = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
_EFFORTS: frozenset[str] = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})

_DEFAULT_MODEL_ID: ModelId = "llama3.2"

_DEFAULT_LLM_MODELS: dict[LlmProvider, str] = {
    "claude": "claude-sonnet-4-5",
    "openai": "gpt-4.1",
    "ollama": "llama3.2",
}

_DEFAULT_LLM_BASE_URLS: dict[LlmProvider, str] = {
    "claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "ollama": "http://127.0.0.1:11434",
}

_PROFILE_PROVIDERS: dict[ModelKind, LlmProvider] = {
    "local": "ollama",
    "gpt": "openai",
}


@dataclass(frozen=True)
class ModelChoice:
    """One model the configuration rail can select."""

    id: ModelId
    label: str
    kind: ModelKind
    model: str


MODEL_CHOICES: tuple[ModelChoice, ...] = (
    ModelChoice(id="llama3.2", label="Llama 3.2", kind="local", model="llama3.2"),
    ModelChoice(id="qwen3:4b", label="Qwen3 4B", kind="local", model="qwen3:4b"),
    ModelChoice(id="gpt-4.1", label="GPT-4.1", kind="gpt", model="gpt-4.1"),
    ModelChoice(id="gpt-4.1-mini", label="GPT-4.1 mini", kind="gpt", model="gpt-4.1-mini"),
    ModelChoice(id="gpt-6-astra", label="GPT-6 Astra", kind="gpt", model="gpt-6-astra"),
)

_CHOICE_BY_ID: dict[str, ModelChoice] = {choice.id: choice for choice in MODEL_CHOICES}


class ModelEndpoint(BaseModel):
    """Optional override for one model key. Blank fields mean the section is unset."""

    model_config = ConfigDict(extra="forbid")

    provider: LlmProvider | None = None
    model: str = ""
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str = ""

    def configured(self) -> bool:
        """Return whether any endpoint field was given a value."""
        if self.provider is not None:
            return True
        if self.model.strip() != "":
            return True
        if self.api_key.get_secret_value().strip() != "":
            return True
        return self.base_url.strip() != ""


class LlmSettings(BaseModel):
    """Which Pydantic AI model a table agent calls.

    ``provider`` is ``claude``, ``openai``, or ``ollama``. An empty ``model`` or
    ``base_url`` uses that provider's default. Claude and OpenAI need
    ``CATALOG_LLM__API_KEY``. Ollama talks to a local server and does not.

    ``profile`` resolves a specific model id. ``llama3.2`` and ``qwen3:4b`` are
    local Ollama models. ``gpt-4.1-mini``, ``gpt-4.1``, and ``gpt-6-astra`` are OpenAI and read
    ``CATALOG_LLM__GPT__API_KEY`` when that section is set. Legacy ids ``local``
    and ``gpt`` still resolve to the matching provider block.
    """

    model_config = ConfigDict(extra="forbid")

    provider: LlmProvider = "ollama"
    model: str = ""
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str = ""
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_tokens: int = Field(default=2048, ge=1, le=200000)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tool_steps: int = Field(default=6, ge=1, le=20)
    effort: ReasoningEffort = "low"
    local: ModelEndpoint = Field(default_factory=ModelEndpoint)
    gpt: ModelEndpoint = Field(default_factory=ModelEndpoint)

    @model_validator(mode="after")
    def _fill_provider_defaults(self) -> LlmSettings:
        model_name: str = self.model.strip()
        if model_name == "":
            model_name = _DEFAULT_LLM_MODELS[self.provider]
        base_url: str = self.base_url.strip().rstrip("/")
        if base_url == "":
            base_url = _DEFAULT_LLM_BASE_URLS[self.provider]
        self.model = model_name
        self.base_url = base_url
        return self

    def default_model_id(self) -> ModelId:
        """Return the catalog id that matches this process, or the local default."""
        current: ModelChoice | None = _CHOICE_BY_ID.get(self.model)
        if current is not None and _PROFILE_PROVIDERS[current.kind] == self.provider:
            return current.id
        if self.provider == "openai":
            return "gpt-4.1"
        return _DEFAULT_MODEL_ID

    def profile(self, model_id: str) -> LlmSettings:
        """Return generation settings for one catalog model."""
        choice: ModelChoice | None = _CHOICE_BY_ID.get(model_id)
        if choice is not None:
            return self._for_choice(choice)
        if model_id == "gpt":
            return self._kind_profile("gpt")
        return self._kind_profile("local")

    def _for_choice(self, choice: ModelChoice) -> LlmSettings:
        endpoint: ModelEndpoint = self.gpt if choice.kind == "gpt" else self.local
        fallback: LlmProvider = _PROFILE_PROVIDERS[choice.kind]
        if endpoint.configured():
            provider: LlmProvider = fallback if endpoint.provider is None else endpoint.provider
            return self._with_endpoint(
                provider,
                choice.model,
                endpoint.api_key,
                endpoint.base_url,
            )
        if self.provider == fallback:
            return self._with_endpoint(fallback, choice.model, self.api_key, self.base_url)
        return self._with_endpoint(fallback, choice.model, SecretStr(""), "")

    def _kind_profile(self, kind: ModelKind) -> LlmSettings:
        endpoint: ModelEndpoint = self.gpt if kind == "gpt" else self.local
        fallback: LlmProvider = _PROFILE_PROVIDERS[kind]
        if endpoint.configured():
            provider: LlmProvider = fallback if endpoint.provider is None else endpoint.provider
            return self._with_endpoint(
                provider,
                endpoint.model,
                endpoint.api_key,
                endpoint.base_url,
            )
        if self.provider == fallback:
            return self._with_endpoint(self.provider, self.model, self.api_key, self.base_url)
        return self._with_endpoint(fallback, "", SecretStr(""), "")

    def _with_endpoint(
        self,
        provider: LlmProvider,
        model: str,
        api_key: SecretStr,
        base_url: str,
    ) -> LlmSettings:
        return LlmSettings(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=self.timeout_seconds,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            max_tool_steps=self.max_tool_steps,
            effort=self.effort,
        )


def coerce_model_id(value: object) -> object:
    """Map a legacy provider key onto a catalog id. Other values pass through."""
    if value == "gpt":
        return "gpt-4.1"
    if value == "local":
        return _DEFAULT_MODEL_ID
    return value


def parse_model_id(value: str) -> ModelId:
    """Return a stored model id. Unknown values use Llama 3.2."""
    coerced: object = coerce_model_id(value)
    if isinstance(coerced, str) and coerced in _CHOICE_BY_ID:
        choice: ModelChoice = _CHOICE_BY_ID[coerced]
        return choice.id
    return _DEFAULT_MODEL_ID


StoredModelId = Annotated[ModelId, BeforeValidator(coerce_model_id)]


def coerce_effort(value: object) -> object:
    """Keep a known effort. Anything else is off."""
    if isinstance(value, str) and value in _EFFORTS:
        return value
    return "off"


def parse_effort(value: str) -> ReasoningEffort:
    """Return a stored effort. Unknown values turn thinking off."""
    coerced: object = coerce_effort(value)
    if coerced == "minimal":
        return "minimal"
    if coerced == "low":
        return "low"
    if coerced == "medium":
        return "medium"
    if coerced == "high":
        return "high"
    if coerced == "xhigh":
        return "xhigh"
    return "off"


StoredEffort = Annotated[ReasoningEffort, BeforeValidator(coerce_effort)]


class PolarisSettings(BaseModel):
    """Apache Polaris REST endpoint and local client credentials."""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(default="http://localhost:8181", min_length=1)
    realm: str = Field(default="POLARIS", min_length=1)
    catalog: str = Field(default="catalog", min_length=1)
    client_id: str = Field(default="root", min_length=1)
    client_secret: str = Field(default="s3cr3t", min_length=1)
    timeout_seconds: float = Field(default=30.0, gt=0)


class AirflowSettings(BaseModel):
    """Timeouts for Airflow 3 REST calls. Instance URLs live in the catalog database."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=30.0, gt=0, le=120)


class ContextSettings(BaseModel):
    """How much catalog text and how many objects a context build may send."""

    model_config = ConfigDict(extra="forbid")

    text_chars: int = Field(default=200, ge=1)
    airflow_source_chars: int = Field(default=3000, ge=1)
    airflow_max_dags: int = Field(default=20, ge=1)
    airflow_max_tasks: int = Field(default=60, ge=1)
    airflow_max_candidate_tables: int = Field(default=300, ge=1)
    max_pipelines: int = Field(default=30, ge=1)
    trace_detail_chars: int = Field(default=2000, ge=1)


class CelerySettings(BaseModel):
    """Redis broker and the beat tick. Job status stays in Postgres.

    A worker holds a Redis lease while a build runs. Beat requeues a running job
    once that lease is gone, and fails the job if it is still abandoned after
    ``abandon_after_seconds``.
    """

    model_config = ConfigDict(extra="forbid")

    broker_url: str = Field(default="redis://127.0.0.1:6379/0", min_length=1)
    dispatch_interval_seconds: float = Field(default=1.0, gt=0)
    task_always_eager: bool = False
    lease_seconds: float = Field(default=90, gt=0)
    reclaim_after_seconds: float = Field(default=120, gt=0)
    abandon_after_seconds: float = Field(default=1800, gt=0)
    visibility_timeout_seconds: float = Field(default=7200, gt=0)

    @model_validator(mode="after")
    def _lease_windows_increase(self) -> CelerySettings:
        if self.lease_seconds >= self.reclaim_after_seconds:
            raise ValueError("lease_seconds must be shorter than reclaim_after_seconds")
        if self.reclaim_after_seconds >= self.abandon_after_seconds:
            raise ValueError("reclaim_after_seconds must be shorter than abandon_after_seconds")
        if self.abandon_after_seconds >= self.visibility_timeout_seconds:
            raise ValueError(
                "abandon_after_seconds must be shorter than visibility_timeout_seconds",
            )
        return self


class AuthSettings(BaseModel):
    """OIDC bearer checks. Disabled keeps a local dev principal and skips tokens."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    issuer: str = ""
    audience: str = "catalog-ui"
    jwks_url: str = ""
    admin_role: str = Field(default="catalog-admin", min_length=1)
    dev_subject: str = Field(default="dev", min_length=1)
    dev_email: str = "dev@localhost"
    dev_name: str = Field(default="Dev", min_length=1)
    dev_is_admin: bool = True

    @model_validator(mode="after")
    def _require_issuer_when_enabled(self) -> AuthSettings:
        if not self.enabled:
            return self
        issuer: str = self.issuer.strip().rstrip("/")
        jwks: str = self.jwks_url.strip()
        if issuer == "" or jwks == "":
            raise ValueError("issuer and jwks_url are required when auth is enabled")
        self.issuer = issuer
        self.jwks_url = jwks
        return self


class AssetDefinitionsSettings(BaseModel):
    """S3 prefix of Python files that declare assets.

    Beat polls the prefix every ``poll_interval_seconds`` when ``enabled`` is true.
    Each pass keeps only assets named by a file under ``assets_folder``. A script
    that disappeared is removed on that pass, the same way Airflow drops a deleted DAG file.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str = ""
    access_key: str = ""
    secret_key: SecretStr = Field(default=SecretStr(""))
    bucket: str = ""
    region: str = ""
    prefix: str = "asset-definitions/"
    assets_folder: str = Field(default="assets", min_length=1)
    poll_interval_seconds: float = Field(default=10.0, gt=0)
    exec_timeout_seconds: float = Field(default=30.0, gt=0)
    max_file_bytes: int = Field(default=1024 * 1024, ge=1)
    executor: Literal["celery", "local"] = "celery"

    @model_validator(mode="after")
    def _normalize_prefix_and_folders(self) -> AssetDefinitionsSettings:
        prefix: str = self.prefix.strip().strip("/")
        self.prefix = f"{prefix}/" if prefix != "" else ""
        folders: tuple[str, ...] = (self.assets_folder,)
        folder: str
        for folder in folders:
            if folder != folder.strip() or "/" in folder or folder in {"", ".", ".."}:
                raise ValueError("definition folder must be a single path segment")
        return self


class ObjectStoreSettings(BaseModel):
    """S3-compatible store for workspace files. An empty endpoint uses memory."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = ""
    access_key: str = ""
    secret_key: SecretStr = Field(default=SecretStr(""))
    bucket: str = Field(default="workspace", min_length=1)
    region: str = Field(default="us-west-2", min_length=1)
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)


class ObservabilitySettings(BaseModel):
    """OTLP export of catalog logs and agent spans. Disabled until configured."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    endpoint: str = Field(default="http://127.0.0.1:5080/api/default", min_length=1)
    service_name: str = Field(default="intelegent-data-catalog", min_length=1)
    username: str = Field(default="root@example.com", min_length=1)
    password: SecretStr = Field(default=SecretStr(""))
    include_content: bool = True

    @model_validator(mode="after")
    def _require_password_when_enabled(self) -> ObservabilitySettings:
        if self.enabled and self.password.get_secret_value().strip() == "":
            raise ValueError("observability password is required when observability is enabled")
        trimmed: str = self.endpoint.strip().rstrip("/")
        if trimmed == "":
            raise ValueError("observability endpoint is required")
        self.endpoint = trimmed
        return self


class CredentialEntry(BaseModel):
    """Username and password for one ``credential_ref``."""

    model_config = ConfigDict(extra="forbid")

    user: str = ""
    password: SecretStr = Field(default=SecretStr(""))


class SeedPostgresSettings(BaseModel):
    """JDBC catalog used when the registry has no Postgres binding with this name."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    name: str = Field(default="appdb", min_length=1)
    host: str = Field(default="postgres", min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="sales", min_length=1)
    credential_ref: str = Field(default="source_db", min_length=1)


class NotebookSettings(BaseModel):
    """Kernel gateway the API proxies for workspace notebooks."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    gateway_url: str = Field(default="http://127.0.0.1:8888", min_length=1)
    gateway_token: SecretStr = Field(default=SecretStr(""))
    ticket_secret: SecretStr = Field(default=SecretStr("dev-notebook-ticket"))
    ticket_ttl_seconds: int = Field(default=60, ge=1)
    max_output_bytes: int = Field(default=1_000_000, ge=1)
    execute_timeout_seconds: float = Field(default=300.0, gt=0)
    kernel_name: str = Field(default="pyspark", min_length=1)
    seed_postgres: SeedPostgresSettings = Field(default_factory=SeedPostgresSettings)


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
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    trino: TrinoSettings = Field(default_factory=TrinoSettings)
    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    marquez: MarquezSettings = Field(default_factory=MarquezSettings)
    polaris: PolarisSettings = Field(default_factory=PolarisSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    airflow: AirflowSettings = Field(default_factory=AirflowSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    object_store: ObjectStoreSettings = Field(default_factory=ObjectStoreSettings)
    asset_definitions: AssetDefinitionsSettings = Field(default_factory=AssetDefinitionsSettings)
    notebook: NotebookSettings = Field(default_factory=NotebookSettings)
    credentials: dict[str, CredentialEntry] = Field(default_factory=dict)

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
