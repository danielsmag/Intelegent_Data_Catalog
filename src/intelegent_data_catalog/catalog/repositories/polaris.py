"""Apache Polaris client for catalog metadata."""

import logging
import threading
import time
from typing import TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from intelegent_data_catalog.catalog.models import ColumnDetail, TableDetail
from intelegent_data_catalog.core.settings import PolarisSettings
from intelegent_data_catalog.exceptions import PolarisError, TableNotFoundError

TModel = TypeVar("TModel", bound=BaseModel)
_MAX_PAGES: int = 100
_TOKEN_SKEW_SECONDS: int = 30


class TokenResponse(BaseModel):
    """OAuth client-credentials token."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    expires_in: int = Field(default=3600, gt=0)


class CatalogEntity(BaseModel):
    """One catalog from the Polaris management API."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)


class CatalogListResponse(BaseModel):
    """Catalog list from the Polaris management API."""

    model_config = ConfigDict(extra="ignore")

    catalogs: list[CatalogEntity]


class NamespaceListResponse(BaseModel):
    """Iceberg namespace page."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    namespaces: list[list[str]] = Field(default_factory=list)
    next_page_token: str | None = Field(default=None, alias="next-page-token")


class TableIdentifier(BaseModel):
    """Iceberg table identifier."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)


class TableListResponse(BaseModel):
    """Iceberg table page."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    identifiers: list[TableIdentifier] | None = None
    next_page_token: str | None = Field(default=None, alias="next-page-token")


class IcebergField(BaseModel):
    """One field in an Iceberg schema."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str
    required: bool = False
    field_type: JsonValue = Field(alias="type")
    doc: str | None = None


class IcebergSchema(BaseModel):
    """Iceberg struct schema."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_id: int = Field(alias="schema-id")
    fields: list[IcebergField]


class IcebergTableMetadata(BaseModel):
    """Subset of Iceberg table metadata used by the explorer."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    location: str | None = None
    last_updated_ms: int | None = Field(default=None, alias="last-updated-ms")
    current_schema_id: int | None = Field(default=None, alias="current-schema-id")
    current_snapshot_id: int | None = Field(default=None, alias="current-snapshot-id")
    schemas: list[IcebergSchema]


class LoadTableResult(BaseModel):
    """Iceberg REST load-table payload."""

    model_config = ConfigDict(extra="ignore")

    metadata: IcebergTableMetadata


def encode_namespace(namespace: str) -> str:
    """Encode a dotted namespace with the Iceberg unit separator."""
    parts: list[str] = namespace.split(".")
    if namespace == "" or any(part == "" for part in parts):
        raise PolarisError(f"invalid namespace: {namespace}")
    encoded_parts: list[str] = [quote(part, safe="") for part in parts]
    return "%1F".join(encoded_parts)


def format_iceberg_type(field_type: JsonValue) -> str:
    """Render an Iceberg field type as a short label."""
    match field_type:
        case str() as primitive:
            return primitive
        case {"type": "decimal", "precision": int(precision), "scale": int(scale)}:
            return f"decimal({precision}, {scale})"
        case {"type": "fixed", "length": int(length)}:
            return f"fixed[{length}]"
        case {"type": "list", "element": element}:
            element_label: str = format_iceberg_type(element)
            return f"list<{element_label}>"
        case {"type": "map", "key": key, "value": value}:
            key_label: str = format_iceberg_type(key)
            value_label: str = format_iceberg_type(value)
            return f"map<{key_label}, {value_label}>"
        case {"type": str(name)}:
            return name
        case _:
            return "unknown"


class PolarisClient:
    """Reads catalog metadata from the Polaris REST API."""

    def __init__(
        self,
        settings: PolarisSettings,
        http_client: httpx.Client,
        logger: logging.Logger,
    ) -> None:
        self._settings: PolarisSettings = settings
        self._http: httpx.Client = http_client
        self._logger: logging.Logger = logger
        self._token: str | None = None
        self._token_deadline: float = 0.0
        self._lock: threading.Lock = threading.Lock()

    def list_catalog_names(self) -> list[str]:
        """Return catalog names from the management API."""
        payload: CatalogListResponse = self._get_model(
            "/api/management/v1/catalogs",
            CatalogListResponse,
            "list catalogs",
        )
        names: list[str] = [catalog.name for catalog in payload.catalogs]
        self._logger.info(
            "polaris catalogs configured=%s found=%s",
            self._settings.catalog,
            ",".join(names),
        )
        return names

    def list_namespace_names(self, catalog: str) -> list[str]:
        """Return dotted namespace names, following Iceberg page tokens."""
        names: list[str] = []
        page_token: str | None = None
        pages: int = 0
        while pages < _MAX_PAGES:
            pages += 1
            params: dict[str, str] | None = None
            if page_token is not None:
                params = {"pageToken": page_token}
            payload: NamespaceListResponse = self._get_model(
                f"/api/catalog/v1/{quote(catalog, safe='')}/namespaces",
                NamespaceListResponse,
                "list namespaces",
                params=params,
            )
            parts: list[str]
            for parts in payload.namespaces:
                if parts:
                    names.append(".".join(parts))
            page_token = payload.next_page_token
            if page_token is None or page_token == "":
                return names
        raise PolarisError(f"polaris namespace listing for {catalog} exceeded {_MAX_PAGES} pages")

    def list_table_names(self, catalog: str, namespace: str) -> list[str]:
        """Return table names in one namespace, following Iceberg page tokens."""
        names: list[str] = []
        page_token: str | None = None
        pages: int = 0
        namespace_path: str = encode_namespace(namespace)
        catalog_path: str = quote(catalog, safe="")
        while pages < _MAX_PAGES:
            pages += 1
            params: dict[str, str] | None = None
            if page_token is not None:
                params = {"pageToken": page_token}
            payload: TableListResponse = self._get_model(
                f"/api/catalog/v1/{catalog_path}/namespaces/{namespace_path}/tables",
                TableListResponse,
                "list tables",
                params=params,
            )
            identifiers: list[TableIdentifier] = payload.identifiers or []
            identifier: TableIdentifier
            for identifier in identifiers:
                names.append(identifier.name)
            page_token = payload.next_page_token
            if page_token is None or page_token == "":
                return names
        raise PolarisError(
            f"polaris table listing for {catalog}.{namespace} exceeded {_MAX_PAGES} pages"
        )

    def load_table(self, catalog: str, namespace: str, table: str) -> TableDetail:
        """Return columns and table metadata from the inline Iceberg metadata."""
        path: str = (
            f"/api/catalog/v1/{quote(catalog, safe='')}"
            f"/namespaces/{encode_namespace(namespace)}"
            f"/tables/{quote(table, safe='')}"
        )
        payload: LoadTableResult = self._get_model(
            path,
            LoadTableResult,
            "load table",
            not_found=TableNotFoundError(catalog, namespace, table),
        )
        schema: IcebergSchema = _current_schema(payload.metadata)
        snapshot_id: str | None = None
        if payload.metadata.current_snapshot_id is not None:
            snapshot_id = str(payload.metadata.current_snapshot_id)
        return TableDetail(
            catalog=catalog,
            namespace=namespace,
            name=table,
            location=payload.metadata.location,
            snapshot_id=snapshot_id,
            last_updated_ms=payload.metadata.last_updated_ms,
            columns=_columns(schema),
        )

    def _get_model(
        self,
        path: str,
        model: type[TModel],
        action: str,
        *,
        params: dict[str, str] | None = None,
        not_found: TableNotFoundError | None = None,
    ) -> TModel:
        response: httpx.Response = self._request("GET", path, action, params=params)
        return self._parse(response, model, action, not_found=not_found)

    def _access_token(self) -> str:
        with self._lock:
            now: float = time.monotonic()
            if self._token is not None and now < self._token_deadline:
                return self._token
            response: httpx.Response = self._request(
                "POST",
                "/api/catalog/v1/oauth/tokens",
                "fetch token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._settings.client_id,
                    "client_secret": self._settings.client_secret,
                    "scope": "PRINCIPAL_ROLE:ALL",
                },
                authenticated=False,
            )
            payload: TokenResponse = self._parse(response, TokenResponse, "fetch token")
            lifetime: int = payload.expires_in
            if lifetime > _TOKEN_SKEW_SECONDS:
                lifetime = lifetime - _TOKEN_SKEW_SECONDS
            self._token = payload.access_token
            self._token_deadline = time.monotonic() + lifetime
            self._logger.debug("fetched polaris access token")
            return self._token

    def _request(
        self,
        method: str,
        path: str,
        action: str,
        *,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Polaris-Realm": self._settings.realm,
        }
        if authenticated:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            return self._http.request(
                method,
                self._url(path),
                headers=headers,
                params=params,
                data=data,
            )
        except httpx.HTTPError as err:
            raise PolarisError(f"polaris {action} failed: {err}") from err

    def _parse(
        self,
        response: httpx.Response,
        model: type[TModel],
        action: str,
        *,
        not_found: TableNotFoundError | None = None,
    ) -> TModel:
        self._require_success(response, action, not_found=not_found)
        try:
            payload: object = response.json()
        except ValueError as err:
            raise PolarisError(f"polaris {action} returned invalid JSON") from err
        try:
            return model.model_validate(payload)
        except ValidationError as err:
            raise PolarisError(f"polaris {action} returned an unexpected payload") from err

    def _require_success(
        self,
        response: httpx.Response,
        action: str,
        *,
        not_found: TableNotFoundError | None = None,
    ) -> None:
        if response.is_success:
            return
        if response.status_code == 404 and not_found is not None:
            raise not_found
        detail: str = response.text.strip()
        if len(detail) > 300:
            detail = detail[:300]
        if detail == "":
            raise PolarisError(f"polaris {action} failed: HTTP {response.status_code}")
        raise PolarisError(f"polaris {action} failed: HTTP {response.status_code}: {detail}")

    def _url(self, path: str) -> str:
        base: str = self._settings.uri.rstrip("/")
        return f"{base}{path}"


def _current_schema(metadata: IcebergTableMetadata) -> IcebergSchema:
    if not metadata.schemas:
        raise PolarisError("polaris table metadata did not include a schema")
    if metadata.current_schema_id is None:
        return metadata.schemas[-1]
    schema: IcebergSchema
    for schema in metadata.schemas:
        if schema.schema_id == metadata.current_schema_id:
            return schema
    raise PolarisError("polaris table metadata current schema was not found")


def _columns(schema: IcebergSchema) -> list[ColumnDetail]:
    columns: list[ColumnDetail] = []
    field: IcebergField
    for field in schema.fields:
        columns.append(
            ColumnDetail(
                name=field.name,
                type=format_iceberg_type(field.field_type),
                required=field.required,
                comment=field.doc,
            )
        )
    return columns
