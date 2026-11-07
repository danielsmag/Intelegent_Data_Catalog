"""Reads and writes asset definition rows. The caller commits."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.persistence.enums import DefinitionItemStatus, DefinitionKind
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetRow,
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

    def list_files(self) -> list[AssetDefinitionFileRow]:
        """Return files in key order."""
        statement = select(AssetDefinitionFileRow).order_by(AssetDefinitionFileRow.key)
        return list(self._session.scalars(statement))

    def file_ids_by_key(self, keys: list[str]) -> dict[str, UUID]:
        """Return the scan row id for each file key that has been scanned."""
        found: dict[str, UUID] = {}
        if len(keys) == 0:
            return found
        statement = select(AssetDefinitionFileRow.key, AssetDefinitionFileRow.id).where(
            AssetDefinitionFileRow.key.in_(keys),
        )
        for record in self._session.execute(statement):
            key: str = record[0]
            file_id: UUID = record[1]
            found[key] = file_id
        return found

    def asset_ids_by_key(self, keys: list[str]) -> dict[str, list[UUID]]:
        """Return asset ids declared by each file key, in item key order."""
        found: dict[str, list[UUID]] = {key: [] for key in keys}
        seen: dict[str, set[UUID]] = {key: set() for key in keys}
        if len(keys) == 0:
            return found
        statement = (
            select(AssetDefinitionFileRow.key, AssetDefinitionItemRow.asset_id)
            .join(
                AssetDefinitionItemRow,
                AssetDefinitionItemRow.file_id == AssetDefinitionFileRow.id,
            )
            .where(AssetDefinitionFileRow.key.in_(keys))
            .where(AssetDefinitionItemRow.kind == DefinitionKind.ASSET.value)
            .where(AssetDefinitionItemRow.asset_id.is_not(None))
            .order_by(AssetDefinitionFileRow.key, AssetDefinitionItemRow.item_key)
        )
        for record in self._session.execute(statement):
            key: str = record[0]
            asset_id: UUID | None = record[1]
            bucket: list[UUID] | None = found.get(key)
            used: set[UUID] | None = seen.get(key)
            if asset_id is None or bucket is None or used is None or asset_id in used:
                continue
            used.add(asset_id)
            bucket.append(asset_id)
        return found

    def items_for(self, file_id: UUID) -> list[AssetDefinitionItemRow]:
        """Return the declarations of one file in key order."""
        statement = (
            select(AssetDefinitionItemRow)
            .where(AssetDefinitionItemRow.file_id == file_id)
            .order_by(AssetDefinitionItemRow.item_key)
        )
        return list(self._session.scalars(statement))

    def unresolved(self) -> list[AssetDefinitionItemRow]:
        """Return unresolved items."""
        statement = select(AssetDefinitionItemRow).where(
            AssetDefinitionItemRow.status == DefinitionItemStatus.UNRESOLVED.value,
        )
        return list(self._session.scalars(statement))

    def applied_asset_ids(self) -> set[UUID]:
        """Return assets an applied declaration still names."""
        statement = select(AssetDefinitionItemRow.asset_id).where(
            AssetDefinitionItemRow.kind == DefinitionKind.ASSET.value,
            AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
            AssetDefinitionItemRow.asset_id.is_not(None),
        )
        found: set[UUID] = set()
        asset_id: UUID | None
        for asset_id in self._session.scalars(statement):
            if asset_id is not None:
                found.add(asset_id)
        return found

    def asset_by_key(self, key: str) -> AssetRow | None:
        """Return the asset stored under ``key``."""
        return self._session.scalar(select(AssetRow).where(AssetRow.key == key))

    def other_applied_claim(self, asset_id: UUID, item_id: UUID) -> bool:
        """True when another applied item still names this asset."""
        statement = select(AssetDefinitionItemRow.id).where(
            AssetDefinitionItemRow.id != item_id,
            AssetDefinitionItemRow.asset_id == asset_id,
            AssetDefinitionItemRow.status == DefinitionItemStatus.APPLIED.value,
        )
        found: UUID | None = self._session.scalar(statement)
        return found is not None

    def delete_item(self, row: AssetDefinitionItemRow) -> None:
        """Delete one declaration. The caller commits."""
        self._session.delete(row)
        self._session.flush()
