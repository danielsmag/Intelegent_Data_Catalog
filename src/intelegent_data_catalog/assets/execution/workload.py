"""One operator run handed to an executor."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.assets.definitions.models import OperatorRef
from intelegent_data_catalog.persistence.enums import OperatorRunTrigger


class OperatorWorkload(BaseModel):
    """Serialized operator run. Safe to send through Celery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: UUID
    asset_id: UUID
    asset_key: str
    file_key: str
    item_key: str
    operators: list[OperatorRef]
    trigger: OperatorRunTrigger = OperatorRunTrigger.APPLY
    force: bool = False
