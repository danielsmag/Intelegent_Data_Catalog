"""Search, tags, and Marquez identity mappings."""

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from intelegent_data_catalog.exceptions import ConflictError, EntityNotFoundError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import TagOrigin
from intelegent_data_catalog.persistence.models import (
    CatalogTableRow,
    ColumnRow,
    MarquezMappingRow,
    TableTagRow,
    TagRow,
)
from intelegent_data_catalog.registry.repository import RegistryRepository


class SearchHit(BaseModel):
    """One PostgreSQL search result. Neo4j is not the search engine."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: UUID
    name: str
    qualified_name: str
    description: str | None


class SearchResponse(BaseModel):
    """Search hits across tables, columns, and tags."""

    model_config = ConfigDict(extra="forbid")

    hits: list[SearchHit]


class TagWrite(BaseModel):
    """Create a tag."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=16000)


class TagRead(BaseModel):
    """Tag returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    description: str | None
    origin: str | None = None


class MarquezMappingWrite(BaseModel):
    """Map a catalog table to a Marquez dataset."""

    model_config = ConfigDict(extra="forbid")

    catalog_table_id: UUID
    marquez_namespace: str = Field(min_length=1, max_length=255)
    marquez_dataset_name: str = Field(min_length=1, max_length=1024)


class MarquezMappingRead(BaseModel):
    """Stored Marquez mapping."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    catalog_table_id: UUID
    marquez_namespace: str
    marquez_dataset_name: str


class CatalogAnnotations:
    """Tags, search, and Marquez mappings on the catalog database."""

    def __init__(self, session: Session) -> None:
        self._repo: RegistryRepository = RegistryRepository(session)

    def search(self, text: str, limit: int = 50) -> SearchResponse:
        """Search catalog text. The query is escaped before LIKE."""
        if text.strip() == "":
            from intelegent_data_catalog.exceptions import ValidationAppError

            raise ValidationAppError("q must not be blank", field="q")
        pattern: str = _like_pattern(text.strip())
        hits: list[SearchHit] = []
        table: CatalogTableRow
        for table in self._repo.search_tables(pattern, limit):
            hits.append(
                SearchHit(
                    kind="table",
                    id=table.id,
                    name=table.name,
                    qualified_name=table.source_key,
                    description=table.description or table.source_description,
                )
            )
        column: ColumnRow
        for column in self._repo.search_columns(pattern, limit):
            parent: CatalogTableRow | None = self._repo.get_table(column.table_id)
            qualified: str = column.name if parent is None else f"{parent.source_key}.{column.name}"
            hits.append(
                SearchHit(
                    kind="column",
                    id=column.id,
                    name=column.name,
                    qualified_name=qualified,
                    description=column.description or column.source_description,
                )
            )
        tag: TagRow
        for tag in self._repo.search_tags(pattern, limit):
            hits.append(
                SearchHit(
                    kind="tag",
                    id=tag.id,
                    name=tag.name,
                    qualified_name=tag.name,
                    description=tag.description,
                )
            )
        return SearchResponse(hits=hits[:limit])

    def create_tag(self, body: TagWrite) -> TagRead:
        """Create a tag."""
        if self._repo.find_tag_by_name(body.name) is not None:
            raise ConflictError(f"tag already exists: {body.name}")
        now = utc_now()
        row: TagRow = TagRow(
            id=uuid4(),
            name=body.name,
            description=body.description,
            created_at=now,
            updated_at=now,
        )
        self._repo._add(row)
        self._repo.commit()
        return TagRead(id=row.id, name=row.name, description=row.description)

    def list_tags(self) -> list[TagRead]:
        """List tags."""
        return [
            TagRead(id=row.id, name=row.name, description=row.description)
            for row in self._repo.list_tags()
        ]

    def attach_tag(self, table_id: UUID, tag_id: UUID) -> TagRead:
        """Attach a user tag. Existing user tags are left in place by sync."""
        if self._repo.get_table(table_id) is None:
            raise EntityNotFoundError("table", str(table_id))
        tag: TagRow | None = self._repo.get_tag(tag_id)
        if tag is None:
            raise EntityNotFoundError("tag", str(tag_id))
        if self._repo.find_table_tag(table_id, tag_id) is not None:
            raise ConflictError("tag is already attached to this table")
        self._repo._add(
            TableTagRow(
                id=uuid4(),
                table_id=table_id,
                tag_id=tag_id,
                origin=TagOrigin.USER.value,
                created_at=utc_now(),
            )
        )
        self._repo.commit()
        return TagRead(
            id=tag.id, name=tag.name, description=tag.description, origin=TagOrigin.USER.value
        )

    def list_table_tags(self, table_id: UUID) -> list[TagRead]:
        """List tags on a table."""
        if self._repo.get_table(table_id) is None:
            raise EntityNotFoundError("table", str(table_id))
        reads: list[TagRead] = []
        attachment: TableTagRow
        for attachment in self._repo.list_table_tags(table_id):
            tag: TagRow | None = self._repo.get_tag(attachment.tag_id)
            if tag is None:
                continue
            reads.append(
                TagRead(
                    id=tag.id,
                    name=tag.name,
                    description=tag.description,
                    origin=attachment.origin,
                )
            )
        return reads

    def create_mapping(self, body: MarquezMappingWrite) -> MarquezMappingRead:
        """Store a Marquez dataset mapping. The catalog id stays a UUID."""
        if self._repo.get_table(body.catalog_table_id) is None:
            raise EntityNotFoundError("table", str(body.catalog_table_id))
        if self._repo.find_marquez_mapping_for_table(body.catalog_table_id) is not None:
            raise ConflictError("table already has a Marquez mapping")
        if (
            self._repo.find_marquez_mapping_for_dataset(
                body.marquez_namespace,
                body.marquez_dataset_name,
            )
            is not None
        ):
            raise ConflictError("Marquez dataset is already mapped")
        now = utc_now()
        row: MarquezMappingRow = MarquezMappingRow(
            id=uuid4(),
            catalog_table_id=body.catalog_table_id,
            marquez_namespace=body.marquez_namespace,
            marquez_dataset_name=body.marquez_dataset_name,
            created_at=now,
            updated_at=now,
        )
        self._repo._add(row)
        self._repo.commit()
        return _mapping_read(row)


def _like_pattern(text: str) -> str:
    escaped: str = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _mapping_read(row: MarquezMappingRow) -> MarquezMappingRead:
    return MarquezMappingRead(
        id=row.id,
        catalog_table_id=row.catalog_table_id,
        marquez_namespace=row.marquez_namespace,
        marquez_dataset_name=row.marquez_dataset_name,
    )
