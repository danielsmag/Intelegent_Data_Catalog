"""Catalog objects returned to the explorer."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ColumnDetail(BaseModel):
    """One column in a table schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    type_name: str = Field(alias="type")
    required: bool
    comment: str | None = None


class TableSummary(BaseModel):
    """Table name shown in the catalog tree."""

    model_config = ConfigDict(extra="forbid")

    name: str


class NamespaceNode(BaseModel):
    """Schema and the tables inside it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    tables: list[TableSummary]


class CatalogNode(BaseModel):
    """Catalog and the schemas inside it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    namespaces: list[NamespaceNode]


class CatalogTree(BaseModel):
    """Full tree for the explorer sidebar."""

    model_config = ConfigDict(extra="forbid")

    catalogs: list[CatalogNode]


class TableDetail(BaseModel):
    """Table metadata shown in the main pane."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    namespace: str
    name: str
    location: str | None
    snapshot_id: str | None
    last_updated_ms: int | None
    columns: list[ColumnDetail]


class QueryColumn(BaseModel):
    """One column in a query result."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    type_name: str = Field(alias="type")


class QueryResult(BaseModel):
    """Rows returned by a sample or a SQL query."""

    model_config = ConfigDict(extra="forbid")

    columns: list[QueryColumn]
    rows: list[list[JsonValue]]
    truncated: bool
