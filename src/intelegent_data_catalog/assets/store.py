"""Latest analysis snapshot for one asset."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.models import AssetAnalysisContext
from intelegent_data_catalog.exceptions import ConflictError, PersistenceUnavailableError
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.models import AssetKnowledgeContextRow
from intelegent_data_catalog.registry.reads import dump_model

_CONFLICT: str = "asset knowledge context conflicts with an existing record"
_INVALID: str = "stored asset knowledge context is invalid"


class AssetKnowledgeStore:
    """One snapshot per asset. A later build replaces the payload."""

    def __init__(self, session: Session) -> None:
        self._session: Session = session

    def save(self, context: AssetAnalysisContext) -> None:
        """Insert or replace the snapshot for ``context.asset_id``."""
        payload: dict[str, object] = dump_model(context)
        now: datetime = utc_now()
        row: AssetKnowledgeContextRow | None = self._by_asset(context.asset_id)
        if row is None:
            created: AssetKnowledgeContextRow = AssetKnowledgeContextRow(
                id=uuid4(),
                asset_id=context.asset_id,
                payload=payload,
                created_at=now,
                updated_at=now,
            )
            self._session.add(created)
        else:
            row.payload = payload
            row.updated_at = now
        self._commit()

    def get(self, asset_id: UUID) -> AssetAnalysisContext | None:
        """Return the snapshot, or none when this asset has never been built."""
        row: AssetKnowledgeContextRow | None = self._by_asset(asset_id)
        if row is None:
            return None
        return self._decode(row.payload, asset_id)

    def _by_asset(self, asset_id: UUID) -> AssetKnowledgeContextRow | None:
        statement = select(AssetKnowledgeContextRow).where(
            AssetKnowledgeContextRow.asset_id == asset_id,
        )
        return self._session.scalar(statement)

    def _decode(self, payload: object, asset_id: UUID) -> AssetAnalysisContext:
        if not isinstance(payload, dict):
            raise PersistenceUnavailableError(_INVALID)
        try:
            context: AssetAnalysisContext = AssetAnalysisContext.model_validate(payload)
        except ValidationError as err:
            raise PersistenceUnavailableError(_INVALID) from err
        if context.asset_id != asset_id:
            raise PersistenceUnavailableError(_INVALID)
        return context

    def _commit(self) -> None:
        try:
            self._session.commit()
        except IntegrityError as err:
            self._session.rollback()
            raise ConflictError(_CONFLICT) from err
