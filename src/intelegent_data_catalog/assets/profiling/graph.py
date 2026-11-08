"""Build the Neo4j profile subgraph for one asset."""

from uuid import UUID

from pydantic import JsonValue

from intelegent_data_catalog.assets.profiling.models import (
    ColumnLink,
    ColumnProfile,
    ProfileDocument,
    TableProfile,
    column_fqn,
    table_fqn,
)
from intelegent_data_catalog.graph.profile_subgraph import (
    ProfileEdgeWrite,
    ProfileNodeWrite,
    ProfileSubgraphWrite,
)


class ProfileGraphBuilder:
    """Turns a profile document into catalog, schema, table, and column nodes."""

    def build(self, owner_asset_id: UUID, document: ProfileDocument) -> ProfileSubgraphWrite:
        """Return the replacement payload owned by ``owner_asset_id``."""
        catalogs: dict[str, ProfileNodeWrite] = {}
        schemas: dict[str, ProfileNodeWrite] = {}
        tables: list[ProfileNodeWrite] = []
        columns: list[ProfileNodeWrite] = []
        edges: list[ProfileEdgeWrite] = []
        schema_counts: dict[str, int] = {}
        profile: TableProfile
        for profile in document.tables:
            schema_id: str = f"{profile.catalog}.{profile.namespace}"
            schema_counts[schema_id] = schema_counts.get(schema_id, 0) + 1
            catalogs.setdefault(
                profile.catalog,
                ProfileNodeWrite(node_id=profile.catalog, name=profile.catalog, properties={}),
            )
            schemas.setdefault(
                schema_id,
                ProfileNodeWrite(node_id=schema_id, name=profile.namespace, properties={}),
            )
            table_id: str = table_fqn(profile.catalog, profile.namespace, profile.table)
            tables.append(
                ProfileNodeWrite(
                    node_id=table_id,
                    name=profile.table,
                    properties=_table_properties(profile),
                ),
            )
            edges.append(
                ProfileEdgeWrite(source=profile.catalog, target=schema_id, kind="HAS_SCHEMA"),
            )
            edges.append(
                ProfileEdgeWrite(source=schema_id, target=table_id, kind="HAS_TABLE"),
            )
            column: ColumnProfile
            for column in profile.columns:
                column_id: str = column_fqn(
                    profile.catalog,
                    profile.namespace,
                    profile.table,
                    column.name,
                )
                columns.append(
                    ProfileNodeWrite(
                        node_id=column_id,
                        name=column.name,
                        properties=_column_properties(column),
                    ),
                )
                edges.append(ProfileEdgeWrite(source=table_id, target=column_id, kind="HAS_COLUMN"))
        schema_id_name: str
        for schema_id_name, node in schemas.items():
            count: int = schema_counts.get(schema_id_name, 0)
            node.properties["table_count"] = count
        catalog_name: str
        catalog_node: ProfileNodeWrite
        for catalog_name, catalog_node in catalogs.items():
            namespaces: int = sum(
                1 for schema_node in schemas if schema_node.startswith(f"{catalog_name}.")
            )
            catalog_node.properties["namespace_count"] = namespaces
        link: ColumnLink
        for link in document.links:
            edges.append(
                ProfileEdgeWrite(
                    source=link.source,
                    target=link.target,
                    kind=link.kind.value,
                    reason=link.reason,
                    score=link.score,
                ),
            )
        return ProfileSubgraphWrite(
            owner_asset_id=str(owner_asset_id),
            catalogs=list(catalogs.values()),
            schemas=list(schemas.values()),
            tables=tables,
            columns=columns,
            edges=_unique_edges(edges),
        )


def _table_properties(profile: TableProfile) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {"column_count": len(profile.columns)}
    if profile.row_count is not None:
        properties["row_count"] = profile.row_count
    return properties


def _column_properties(column: ColumnProfile) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {
        "iceberg_type": column.iceberg_type,
        "candidate_key": column.candidate_key,
    }
    _put(properties, "null_count", column.null_count)
    _put(properties, "null_ratio", column.null_ratio)
    _put(properties, "distinct_count", column.distinct_count)
    _put(properties, "uniqueness_ratio", column.uniqueness_ratio)
    _put(properties, "minimum", column.minimum)
    _put(properties, "maximum", column.maximum)
    _put(properties, "mean", column.mean)
    _put(properties, "stddev", column.stddev)
    _put(properties, "avg_length", column.avg_length)
    _put(properties, "max_length", column.max_length)
    _put(properties, "true_count", column.true_count)
    return properties


def _put(properties: dict[str, JsonValue], name: str, value: JsonValue | None) -> None:
    if value is not None:
        properties[name] = value


def _unique_edges(edges: list[ProfileEdgeWrite]) -> list[ProfileEdgeWrite]:
    seen: set[tuple[str, str, str]] = set()
    found: list[ProfileEdgeWrite] = []
    edge: ProfileEdgeWrite
    for edge in edges:
        key: tuple[str, str, str] = (edge.source, edge.target, edge.kind)
        if key in seen:
            continue
        seen.add(key)
        found.append(edge)
    return found
