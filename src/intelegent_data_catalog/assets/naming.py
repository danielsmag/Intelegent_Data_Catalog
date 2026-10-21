"""Slugs for assets inside one workspace and owner."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.persistence.models import AssetRow

_SLUG_LIMIT: int = 200


class AssetSlugs:
    """Allocates a unique slug for one workspace and owner."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def claim(
        self,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        raw: str,
        current_id: UUID | None = None,
    ) -> str:
        """Return a slug that does not collide with another asset in the same bucket."""
        used: set[str] = self._taken(workspace_id, owner_user_id, current_id)
        cleaned: str = "".join(char.lower() if char.isalnum() else "-" for char in raw).strip("-")
        if cleaned == "":
            cleaned = "asset"
        cleaned = cleaned[:_SLUG_LIMIT]
        candidate: str = cleaned
        number: int = 2
        while candidate in used:
            suffix: str = f"-{number}"
            candidate = f"{cleaned[: _SLUG_LIMIT - len(suffix)]}{suffix}"
            number += 1
        return candidate

    def _taken(
        self,
        workspace_id: UUID,
        owner_user_id: UUID | None,
        current_id: UUID | None,
    ) -> set[str]:
        statement = select(AssetRow.slug, AssetRow.id).where(AssetRow.workspace_id == workspace_id)
        if owner_user_id is None:
            statement = statement.where(AssetRow.owner_user_id.is_(None))
        else:
            statement = statement.where(AssetRow.owner_user_id == owner_user_id)
        taken: set[str] = set()
        row: tuple[str, UUID]
        for row in self._session.execute(statement):
            if current_id is not None and row[1] == current_id:
                continue
            taken.add(row[0])
        return taken
