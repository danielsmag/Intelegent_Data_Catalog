"""Table asset. Context builds go through the existing table agent."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import Asset, AssetAgent
from intelegent_data_catalog.assets.models import AssetAnalysisContext, AssetContextOptions
from intelegent_data_catalog.exceptions import EntityNotFoundError, PersistenceUnavailableError
from intelegent_data_catalog.knowledge.models import TableAnalysisContext, TableContextOptions
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.models import CatalogTableRow
from intelegent_data_catalog.registry.reads import dump_model

_JSON_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])

_TABLE_OPTIONS: dict[str, bool] = {
    "include_graph": True,
    "include_lineage": True,
    "include_data_profile": False,
}


class TableAssetDetail(BaseModel):
    """Catalog table behind a table asset."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    source_key: str
    table_format: str | None
    scope_id: UUID | None


class TableAssetAgentAdapter(AssetAgent):
    """Maps asset build and describe calls onto ``TableAgent``."""

    def __init__(self, table_id: UUID, agent: TableAgent, session: Session) -> None:
        self._table_id: UUID = table_id
        self._agent: TableAgent = agent
        self._session: Session = session

    def get_metadata(self) -> BaseModel:
        """Load the table catalog context."""
        return self._agent.get_metadata()

    def build_context(self, options: AssetContextOptions) -> AssetAnalysisContext:
        """Build the table snapshot and store it on the asset."""
        flags: dict[str, bool] = options.flags
        built: TableAnalysisContext = self._agent.build_context(
            TableContextOptions(
                include_data_profile=flags.get("include_data_profile", False),
                include_graph=flags.get("include_graph", True),
                include_lineage=flags.get("include_lineage", True),
                model=options.model,
                effort=options.effort,
            ),
        )
        table: CatalogTableRow | None = self._session.get(CatalogTableRow, self._table_id)
        asset_id: UUID | None = None if table is None else table.asset_id
        if asset_id is None:
            raise EntityNotFoundError("asset", str(self._table_id))
        summary: str | None = built.table.description or built.table.name
        return AssetAnalysisContext(
            asset_id=asset_id,
            kind=AssetKind.TABLE,
            summary=summary,
            built_at=utc_now(),
            payload=_analysis_payload(built),
        )

    def describe(self) -> str:
        """Write the table's business context through the table agent."""
        return self._agent.describe_table()


class TableAsset(Asset):
    """A catalog table addressed as an asset."""

    def default_options(self) -> dict[str, bool]:
        """Flags for a table context build."""
        return dict(_TABLE_OPTIONS)

    def agent(self) -> AssetAgent:
        """Adapter over the table agent for this asset's table."""
        table: CatalogTableRow = self._table()
        return self._agents.for_table(table.id)

    def detail(self) -> TableAssetDetail:
        """Table id and source key."""
        table: CatalogTableRow = self._table()
        return TableAssetDetail(
            table_id=table.id,
            source_key=table.source_key,
            table_format=table.table_format,
            scope_id=table.scope_id,
        )

    def _table(self) -> CatalogTableRow:
        statement = select(CatalogTableRow).where(CatalogTableRow.asset_id == self.id)
        table: CatalogTableRow | None = self._session.scalar(statement)
        if table is None:
            raise EntityNotFoundError("catalog table", str(self.id))
        return table


def _analysis_payload(built: TableAnalysisContext) -> dict[str, JsonValue]:
    """JSON object stored on the asset for one table snapshot."""
    raw: dict[str, object] = dump_model(built)
    try:
        payload: dict[str, JsonValue] = _JSON_OBJECT.validate_python(raw)
    except ValidationError as err:
        raise PersistenceUnavailableError("stored catalog JSON is invalid") from err
    return payload
