"""Marquez lineage as dependency context. A job edge is not a join."""

from typing import Protocol
from uuid import UUID

from intelegent_data_catalog.graph.projection import LineageLink
from intelegent_data_catalog.knowledge.models import (
    DatasetIdentity,
    LineageContext,
    LineageDataset,
    LineageJob,
)
from intelegent_data_catalog.lineage.marquez import LineageReader
from intelegent_data_catalog.persistence.models import MarquezMappingRow
from intelegent_data_catalog.registry.repository import RegistryRepository


class DatasetCatalog(Protocol):
    """Maps catalog table ids to Marquez datasets and back."""

    def dataset_for_table(self, table_id: UUID) -> DatasetIdentity | None:
        """Return the mapped dataset, or none when the table has no mapping."""
        ...

    def table_for_dataset(self, namespace: str, name: str) -> UUID | None:
        """Return the catalog table id for a dataset, or none when it is unmapped."""
        ...


class LineageContextProvider(Protocol):
    """Supplies lineage around one catalog table."""

    def get_lineage(self, table_id: UUID) -> LineageContext:
        """Return upstream datasets, downstream datasets, and the jobs between them."""
        ...


class PostgresDatasetCatalog:
    """Read Marquez mappings stored on the catalog tables."""

    def __init__(self, repository: RegistryRepository) -> None:
        self._repository: RegistryRepository = repository

    def dataset_for_table(self, table_id: UUID) -> DatasetIdentity | None:
        """Return the mapping for one table."""
        row: MarquezMappingRow | None = self._repository.find_marquez_mapping_for_table(table_id)
        if row is None:
            return None
        return DatasetIdentity(
            catalog_table_id=row.catalog_table_id,
            namespace=row.marquez_namespace,
            name=row.marquez_dataset_name,
        )

    def table_for_dataset(self, namespace: str, name: str) -> UUID | None:
        """Return the catalog id mapped to one Marquez dataset."""
        row: MarquezMappingRow | None = self._repository.find_marquez_mapping_for_dataset(
            namespace,
            name,
        )
        if row is None:
            return None
        return row.catalog_table_id


class MarquezLineageContextProvider:
    """Turn job input and output links into lineage context for one table.

    ``A → job → B`` means B depends on A. It does not mean ``A.id = B.id``.
    """

    def __init__(self, reader: LineageReader, datasets: DatasetCatalog) -> None:
        self._reader: LineageReader = reader
        self._datasets: DatasetCatalog = datasets

    def get_lineage(self, table_id: UUID) -> LineageContext:
        """Return an empty context when the table has no Marquez mapping."""
        identity: DatasetIdentity | None = self._datasets.dataset_for_table(table_id)
        upstream: list[LineageDataset] = []
        downstream: list[LineageDataset] = []
        producers: list[LineageJob] = []
        consumers: list[LineageJob] = []
        if identity is None:
            return _context(table_id, upstream, downstream, producers, consumers)
        link: LineageLink
        for link in self._reader.read_links():
            output_matches: bool = _same_dataset(link.output_namespace, link.output_name, identity)
            input_matches: bool = _same_dataset(link.input_namespace, link.input_name, identity)
            if output_matches:
                _append_job(producers, link.job_namespace, link.job_name)
                if not input_matches:
                    _append_dataset(
                        upstream,
                        self._datasets,
                        link.input_namespace,
                        link.input_name,
                    )
            if input_matches:
                _append_job(consumers, link.job_namespace, link.job_name)
                if not output_matches:
                    _append_dataset(
                        downstream,
                        self._datasets,
                        link.output_namespace,
                        link.output_name,
                    )
        return _context(table_id, upstream, downstream, producers, consumers)


def _context(
    table_id: UUID,
    upstream: list[LineageDataset],
    downstream: list[LineageDataset],
    producers: list[LineageJob],
    consumers: list[LineageJob],
) -> LineageContext:
    return LineageContext(
        table_id=table_id,
        upstream_datasets=upstream,
        downstream_datasets=downstream,
        producer_jobs=producers,
        consumer_jobs=consumers,
    )


def _same_dataset(namespace: str, name: str, identity: DatasetIdentity) -> bool:
    return namespace == identity.namespace and name == identity.name


def _append_dataset(
    found: list[LineageDataset],
    datasets: DatasetCatalog,
    namespace: str,
    name: str,
) -> None:
    if any(item.namespace == namespace and item.name == name for item in found):
        return
    catalog_table_id: UUID | None = datasets.table_for_dataset(namespace, name)
    found.append(
        LineageDataset(
            namespace=namespace,
            name=name,
            catalog_table_id=catalog_table_id,
            resolved=catalog_table_id is not None,
        )
    )


class EmptyLineageReader:
    """Lineage stand-in used when Marquez is not configured."""

    def read_links(self) -> list[LineageLink]:
        """Return no job links."""
        return []


def _append_job(found: list[LineageJob], namespace: str, name: str) -> None:
    if any(item.namespace == namespace and item.name == name for item in found):
        return
    found.append(LineageJob(namespace=namespace, name=name))
