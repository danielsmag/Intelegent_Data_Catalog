"""Reads and writes definition file rows. The caller commits."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.definitions.layout import KIND_RANK
from intelegent_data_catalog.persistence.enums import DefinitionItemStatus, DefinitionKind
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
)


class AssetDefinitionRepository:
    """Definition file persistence."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def get_by_key(self, key: str) -> AssetDefinitionFileRow | None:
        """Return the file stored at ``key``."""
        return self._session.scalar(
            select(AssetDefinitionFileRow).where(AssetDefinitionFileRow.key == key),
        )

    def get(self, file_id: UUID) -> AssetDefinitionFileRow | None:
        """Return one file."""
        return self._session.get(AssetDefinitionFileRow, file_id)

    def add_file(self, row: AssetDefinitionFileRow) -> AssetDefinitionFileRow:
        """Insert a file. The caller commits."""
        self._session.add(row)
        self._session.flush()
        return row

    def list_files(self, folder: DefinitionKind | None) -> list[AssetDefinitionFileRow]:
        """Return files in key order. ``folder`` limits the kind."""
        statement = select(AssetDefinitionFileRow).order_by(AssetDefinitionFileRow.key)
        if folder is not None:
            statement = statement.where(AssetDefinitionFileRow.folder_kind == folder.value)
        return list(self._session.scalars(statement))

    def items_for(self, file_id: UUID) -> list[AssetDefinitionItemRow]:
        """Return the declarations of one file in key order."""
        statement = (
            select(AssetDefinitionItemRow)
            .where(AssetDefinitionItemRow.file_id == file_id)
            .order_by(AssetDefinitionItemRow.item_key)
        )
        return list(self._session.scalars(statement))

    def unresolved(self) -> list[AssetDefinitionItemRow]:
        """Return unresolved items, spaces first, then scopes, then assets."""
        statement = select(AssetDefinitionItemRow).where(
            AssetDefinitionItemRow.status == DefinitionItemStatus.UNRESOLVED.value,
        )
        rows: list[AssetDefinitionItemRow] = list(self._session.scalars(statement))
        rows.sort(key=_item_sort)
        return rows

    def other_applied_claim(self, item: AssetDefinitionItemRow) -> bool:
        """True when another applied item still holds this asset membership."""
        if item.asset_id is None:
            return False
        statement = select(AssetDefinitionItemRow.id).where(
            AssetDefinitionItemRow.id != item.id,
            AssetDefinitionItemRow.asset_id == item.asset_id,
            AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
        )
        if item.scope_id is not None:
            statement = statement.where(AssetDefinitionItemRow.scope_id == item.scope_id)
        else:
            statement = statement.where(AssetDefinitionItemRow.space_id == item.space_id)
        found: UUID | None = self._session.scalar(statement)
        return found is not None

    def delete_item(self, row: AssetDefinitionItemRow) -> None:
        """Delete one declaration. The caller commits."""
        self._session.delete(row)
        self._session.flush()


def _item_sort(row: AssetDefinitionItemRow) -> tuple[int, str]:
    rank: int = KIND_RANK.get(row.kind, 9)
    return (rank, row.item_key)
