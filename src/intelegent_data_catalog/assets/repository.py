"""Reads asset rows. The caller commits."""

from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from intelegent_data_catalog.persistence.enums import AssetKind, DefinitionItemStatus
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionItemRow,
    AssetOutputRow,
    AssetRow,
)


class AssetRepository:
    """Asset persistence used by the API."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def get(self, asset_id: UUID) -> AssetRow | None:
        """Return one asset."""
        return self._session.get(AssetRow, asset_id)

    def search(
        self,
        *,
        text: str | None,
        kind: AssetKind | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> tuple[list[AssetRow], int]:
        """Return a page of assets and the unpaged total."""
        filters: list[ColumnElement[bool]] = []
        if kind is not None:
            filters.append(AssetRow.kind == kind.value)
        if text is not None and text.strip() != "":
            pattern: str = f"%{text.strip()}%"
            filters.append(
                or_(
                    AssetRow.key.ilike(pattern),
                    AssetRow.name.ilike(pattern),
                    AssetRow.description.ilike(pattern),
                )
            )
        count_stmt = select(func.count()).select_from(AssetRow)
        statement: Select[tuple[AssetRow]] = _ordered(select(AssetRow), sort)
        if len(filters) > 0:
            count_stmt = count_stmt.where(*filters)
            statement = statement.where(*filters)
        total: int = int(self._session.scalar(count_stmt) or 0)
        rows: list[AssetRow] = list(self._session.scalars(statement.limit(limit).offset(offset)))
        return rows, total

    def applied_item(self, asset_id: UUID) -> AssetDefinitionItemRow | None:
        """Return the newest applied declaration for one asset."""
        statement = (
            select(AssetDefinitionItemRow)
            .where(
                AssetDefinitionItemRow.asset_id == asset_id,
                AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
            )
            .order_by(AssetDefinitionItemRow.updated_at.desc())
        )
        return self._session.scalar(statement)

    def schedules(
        self,
        asset_ids: list[UUID],
    ) -> dict[UUID, AssetDefinitionItemRow]:
        """Return one applied item per asset."""
        if len(asset_ids) == 0:
            return {}
        statement = (
            select(AssetDefinitionItemRow)
            .where(
                AssetDefinitionItemRow.asset_id.in_(asset_ids),
                AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
            )
            .order_by(AssetDefinitionItemRow.updated_at.desc())
        )
        found: dict[UUID, AssetDefinitionItemRow] = {}
        row: AssetDefinitionItemRow
        for row in self._session.scalars(statement):
            if row.asset_id is None or row.asset_id in found:
                continue
            found[row.asset_id] = row
        return found

    def outputs(self, asset_id: UUID) -> list[AssetOutputRow]:
        """Return stored outputs, newest first."""
        statement = (
            select(AssetOutputRow)
            .where(AssetOutputRow.asset_id == asset_id)
            .order_by(AssetOutputRow.created_at.desc())
        )
        return list(self._session.scalars(statement))

    def output(self, asset_id: UUID, output_id: UUID) -> AssetOutputRow | None:
        """Return one output when it belongs to ``asset_id``."""
        row: AssetOutputRow | None = self._session.get(AssetOutputRow, output_id)
        if row is None or row.asset_id != asset_id:
            return None
        return row


def _ordered(statement: Select[tuple[AssetRow]], sort: str) -> Select[tuple[AssetRow]]:
    """Sort a page. Name breaks ties."""
    if sort == "kind":
        return statement.order_by(AssetRow.kind.asc(), AssetRow.name.asc())
    if sort == "updated":
        return statement.order_by(AssetRow.updated_at.desc(), AssetRow.name.asc())
    return statement.order_by(AssetRow.name.asc())
