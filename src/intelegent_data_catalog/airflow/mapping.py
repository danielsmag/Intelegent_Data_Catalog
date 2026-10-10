"""Match Airflow DAGs to catalog tables. MANUAL links are not produced here."""

import re
from dataclasses import dataclass
from uuid import UUID

from intelegent_data_catalog.persistence.enums import (
    AirflowMappingDirection,
    AirflowMappingOrigin,
)

_EDGE: str = r"[\w.]"


@dataclass(frozen=True)
class DagView:
    """Text a sync can search for table names."""

    dag_id: str
    description: str | None
    source_code: str | None


@dataclass(frozen=True)
class AssetView:
    """One Airflow asset and the DAGs Airflow already attached to it."""

    uri: str
    producing_dag_ids: frozenset[str]
    consuming_dag_ids: frozenset[str]
    scheduled_dag_ids: frozenset[str]


@dataclass(frozen=True)
class TableView:
    """Physical name of one catalog table."""

    table_id: UUID
    catalog: str
    namespace: str
    table: str


@dataclass(frozen=True)
class MappingCandidate:
    """Discovered link. Origin is ASSET or SOURCE_MATCH."""

    dag_id: str
    table_id: UUID
    origin: AirflowMappingOrigin
    direction: AirflowMappingDirection
    evidence: str


def match_dag_tables(
    dags: list[DagView],
    assets: list[AssetView],
    tables: list[TableView],
) -> list[MappingCandidate]:
    """Return asset and source-text links. One row per DAG, table, and origin."""
    found: dict[tuple[str, UUID, AirflowMappingOrigin], MappingCandidate] = {}
    dag: DagView
    for dag in dags:
        haystack: str = _haystack(dag)
        table: TableView
        for table in tables:
            _match_assets(found, dag, table, assets)
            token: str | None = _first_token(haystack, _source_tokens(table))
            if token is None:
                continue
            key: tuple[str, UUID, AirflowMappingOrigin] = (
                dag.dag_id,
                table.table_id,
                AirflowMappingOrigin.SOURCE_MATCH,
            )
            found[key] = MappingCandidate(
                dag_id=dag.dag_id,
                table_id=table.table_id,
                origin=AirflowMappingOrigin.SOURCE_MATCH,
                direction=AirflowMappingDirection.UNKNOWN,
                evidence=token,
            )
    ordered: list[MappingCandidate] = list(found.values())
    ordered.sort(key=lambda item: (item.dag_id, item.origin.value, str(item.table_id)))
    return ordered


def _match_assets(
    found: dict[tuple[str, UUID, AirflowMappingOrigin], MappingCandidate],
    dag: DagView,
    table: TableView,
    assets: list[AssetView],
) -> None:
    asset: AssetView
    for asset in assets:
        direction: AirflowMappingDirection | None = _direction(dag.dag_id, asset)
        if direction is None:
            continue
        token: str | None = _first_token(asset.uri, _asset_tokens(table))
        if token is None:
            continue
        key: tuple[str, UUID, AirflowMappingOrigin] = (
            dag.dag_id,
            table.table_id,
            AirflowMappingOrigin.ASSET,
        )
        if key in found:
            continue
        found[key] = MappingCandidate(
            dag_id=dag.dag_id,
            table_id=table.table_id,
            origin=AirflowMappingOrigin.ASSET,
            direction=direction,
            evidence=asset.uri,
        )


def _direction(dag_id: str, asset: AssetView) -> AirflowMappingDirection | None:
    if dag_id in asset.producing_dag_ids:
        return AirflowMappingDirection.OUTPUT
    if dag_id in asset.consuming_dag_ids or dag_id in asset.scheduled_dag_ids:
        return AirflowMappingDirection.INPUT
    return None


def _haystack(dag: DagView) -> str:
    parts: list[str] = []
    if dag.description is not None:
        parts.append(dag.description)
    if dag.source_code is not None:
        parts.append(dag.source_code)
    return "\n".join(parts)


def _source_tokens(table: TableView) -> list[str]:
    qualified: str = f"{table.catalog}.{table.namespace}.{table.table}"
    short: str = f"{table.namespace}.{table.table}"
    return [qualified, short]


def _asset_tokens(table: TableView) -> list[str]:
    return [
        f"{table.catalog}/{table.namespace}/{table.table}",
        f"{table.catalog}.{table.namespace}.{table.table}",
        f"{table.namespace}/{table.table}",
        f"{table.namespace}.{table.table}",
    ]


def _first_token(text: str, tokens: list[str]) -> str | None:
    token: str
    for token in tokens:
        if _contains_token(text, token):
            return token
    return None


def _contains_token(text: str, token: str) -> bool:
    if token == "":
        return False
    # A trailing period ends a sentence. A period plus a word continues a name.
    pattern: re.Pattern[str] = re.compile(
        rf"(?<!{_EDGE}){re.escape(token)}(?!\w)(?!\.\w)",
    )
    return pattern.search(text) is not None
