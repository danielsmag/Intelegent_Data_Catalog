"""Build the environment a notebook kernel receives at start."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.notebooks.credentials import Credential, CredentialResolver
from intelegent_data_catalog.persistence.enums import (
    DataSourceType,
    EngineConnectionStatus,
    EngineType,
)
from intelegent_data_catalog.persistence.models import (
    DataSourceRow,
    EngineConnectionRow,
    ScopeRow,
    SourceEngineBindingRow,
)

_POSTGRES_DRIVER: str = "org.postgresql.Driver"
_RESERVED: frozenset[str] = frozenset({"catalog", "spark_catalog", "system", "spark"})
_SLUG: re.Pattern[str] = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class KernelEnvironment:
    """Strings passed to the kernel, plus the names safe to show in the UI."""

    variables: dict[str, str]
    engines: tuple[str, ...]
    spark_catalogs: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass
class _EngineDraft:
    name: str
    host: str
    port: int
    default: bool = False


@dataclass
class _CatalogDraft:
    name: str
    url: str = ""
    user: str = ""
    password: str = ""
    error: str = ""
    driver: str = _POSTGRES_DRIVER


@dataclass
class _Collected:
    engines: list[_EngineDraft] = field(default_factory=list)
    catalogs: list[_CatalogDraft] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    used_names: set[str] = field(default_factory=lambda: set(_RESERVED))


class KernelEnvBuilder:
    """Turns registry rows into per-kernel environment variables."""

    def __init__(
        self,
        session: Session,
        settings: AppSettings,
        resolver: CredentialResolver,
    ) -> None:
        self._session: Session = session
        self._settings: AppSettings = settings
        self._resolver: CredentialResolver = resolver

    def build(self, access: AccessContext) -> KernelEnvironment:
        """Collect Trino engines and Postgres JDBC catalogs visible to this caller."""
        collected: _Collected = _Collected()
        self._engines(collected)
        self._postgres(collected, access)
        self._seed_postgres(collected)
        self._mark_default_engine(collected)
        return KernelEnvironment(
            variables={
                "CATALOG_USER": access.principal.subject,
                "CATALOG_ENGINES_JSON": _engines_json(collected.engines),
                "CATALOG_SPARK_CATALOGS_JSON": _catalogs_json(collected.catalogs),
            },
            engines=tuple(item.name for item in collected.engines),
            spark_catalogs=tuple(item.name for item in collected.catalogs if item.error == ""),
            warnings=tuple(collected.warnings),
        )

    def _engines(self, collected: _Collected) -> None:
        statement = (
            select(EngineConnectionRow)
            .where(EngineConnectionRow.engine_type == EngineType.TRINO.value)
            .where(EngineConnectionRow.status == EngineConnectionStatus.ACTIVE.value)
            .order_by(EngineConnectionRow.name.asc())
        )
        used: set[str] = set()
        row: EngineConnectionRow
        for row in self._session.scalars(statement):
            parsed: tuple[str, int] | None = _endpoint(row.endpoint)
            if parsed is None:
                collected.warnings.append(f"Trino connection {row.name!r} has no usable endpoint")
                continue
            slug: str | None = _claim(_slug(row.name), used)
            if slug is None:
                collected.warnings.append(f"Trino connection {row.name!r} has no usable name")
                continue
            if slug != _slug(row.name):
                collected.warnings.append(f"Trino connection {row.name!r} registered as {slug}")
            host, port = parsed
            collected.engines.append(
                _EngineDraft(
                    name=slug,
                    host=host,
                    port=port,
                    default=_flag(row.configuration.get("default")),
                )
            )
        if len(collected.engines) == 0 and self._settings.trino.enabled:
            collected.engines.append(
                _EngineDraft(
                    name="trino",
                    host=self._settings.trino.host,
                    port=self._settings.trino.port,
                    default=True,
                )
            )

    def _postgres(self, collected: _Collected, access: AccessContext) -> None:
        statement = (
            select(SourceEngineBindingRow, DataSourceRow)
            .join(DataSourceRow, DataSourceRow.id == SourceEngineBindingRow.data_source_id)
            .join(
                EngineConnectionRow,
                EngineConnectionRow.id == SourceEngineBindingRow.engine_connection_id,
            )
            .where(DataSourceRow.type == DataSourceType.POSTGRES.value)
            .where(EngineConnectionRow.status == EngineConnectionStatus.ACTIVE.value)
            .order_by(SourceEngineBindingRow.binding_key.asc())
        )
        binding: SourceEngineBindingRow
        source: DataSourceRow
        for binding, source in self._session.execute(statement):
            if not self._visible(binding, access):
                continue
            self._add_postgres(
                collected,
                raw_name=binding.binding_key,
                configuration=source.configuration,
                credential_ref=source.credential_ref,
            )

    def _seed_postgres(self, collected: _Collected) -> None:
        seed = self._settings.notebook.seed_postgres
        if not seed.enabled:
            return
        slug: str | None = _slug(seed.name)
        if slug is None or slug in collected.used_names:
            return
        seed_configuration: dict[str, object] = {
            "host": seed.host,
            "port": seed.port,
            "database": seed.database,
        }
        self._add_postgres(
            collected,
            raw_name=seed.name,
            configuration=seed_configuration,
            credential_ref=seed.credential_ref,
        )

    def _add_postgres(
        self,
        collected: _Collected,
        raw_name: str,
        configuration: Mapping[str, object],
        credential_ref: str | None,
    ) -> None:
        slug: str | None = _slug(raw_name)
        chosen: str | None = _claim(slug, collected.used_names)
        if chosen is None:
            collected.warnings.append(f"Postgres binding {raw_name!r} has no usable catalog name")
            return
        if chosen != slug:
            collected.warnings.append(f"spark catalog {raw_name!r} registered as {chosen}")
        located: tuple[str, int, str] | None = _postgres_target(configuration)
        secret: Credential | None = self._resolver.resolve(credential_ref)
        username: str = ""
        password: str = ""
        if secret is not None:
            configured_user: str | None = _text(configuration.get("username"))
            username = secret.user if secret.user.strip() != "" else (configured_user or "")
            password = secret.password
        if located is None or username == "" or password == "":
            message: str = f"missing connection or credentials for {raw_name}"
            collected.warnings.append(message)
            collected.catalogs.append(_CatalogDraft(name=chosen, error=message))
            return
        host, port, database = located
        collected.catalogs.append(
            _CatalogDraft(
                name=chosen,
                url=f"jdbc:postgresql://{host}:{port}/{database}",
                user=username,
                password=password,
            )
        )

    def _visible(self, binding: SourceEngineBindingRow, access: AccessContext) -> bool:
        if binding.default_scope_id is None or access.visible_space_ids is None:
            return True
        scope: ScopeRow | None = self._session.get(ScopeRow, binding.default_scope_id)
        if scope is None:
            return False
        visible: frozenset[UUID] = access.visible_space_ids
        return scope.space_id in visible

    def _mark_default_engine(self, collected: _Collected) -> None:
        if len(collected.engines) == 0:
            return
        defaults: list[_EngineDraft] = [item for item in collected.engines if item.default]
        if len(defaults) == 1:
            return
        if len(defaults) > 1:
            first: _EngineDraft = defaults[0]
            item: _EngineDraft
            for item in defaults[1:]:
                item.default = False
            collected.warnings.append(f"several default Trino engines; using {first.name}")
            return
        collected.engines[0].default = True
        if len(collected.engines) > 1:
            collected.warnings.append(
                f"no default Trino engine; using {collected.engines[0].name}"
            )


def _engines_json(engines: list[_EngineDraft]) -> str:
    payload: list[dict[str, object]] = [
        {"name": item.name, "host": item.host, "port": item.port, "default": item.default}
        for item in engines
    ]
    return json.dumps(payload, separators=(",", ":"))


def _catalogs_json(catalogs: list[_CatalogDraft]) -> str:
    payload: list[dict[str, object]] = []
    item: _CatalogDraft
    for item in catalogs:
        if item.error != "":
            payload.append({"name": item.name, "error": item.error})
            continue
        payload.append(
            {
                "name": item.name,
                "url": item.url,
                "driver": item.driver,
                "user": item.user,
                "password": item.password,
            }
        )
    return json.dumps(payload, separators=(",", ":"))


def _slug(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned: str = _SLUG.sub("_", raw.strip().lower().replace("-", "_"))
    cleaned = cleaned.strip("_")
    if cleaned == "":
        return None
    if cleaned[0].isdigit():
        cleaned = f"src_{cleaned}"
    return cleaned


def _claim(slug: str | None, used: set[str]) -> str | None:
    if slug is None:
        return None
    if slug not in used:
        used.add(slug)
        return slug
    suffix: int = 2
    while suffix < 50:
        candidate: str = f"{slug}_{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        suffix += 1
    return None


def _endpoint(endpoint: str) -> tuple[str, int] | None:
    raw: str = endpoint.strip()
    if raw == "":
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed: SplitResult = urlsplit(raw)
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        return None
    port: int = parsed.port if parsed.port is not None else 80
    return parsed.hostname, port


def _postgres_target(configuration: Mapping[str, object]) -> tuple[str, int, str] | None:
    host: str | None = _text(configuration.get("host"))
    database: str | None = _text(configuration.get("database"))
    if host is None or database is None:
        return None
    if any(mark in host for mark in ("/", "@", " ", "?")):
        return None
    if any(mark in database for mark in ("/", "?", " ")):
        return None
    port: int = _port(configuration.get("port", 5432))
    if port < 1 or port > 65535:
        return None
    return host, port, database


def _port(value: object) -> int:
    if isinstance(value, bool):
        return 5432
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 5432


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip() != "":
        return value.strip()
    return None


def _flag(value: object) -> bool:
    return value is True
