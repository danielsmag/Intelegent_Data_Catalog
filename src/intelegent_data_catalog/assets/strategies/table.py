"""Table kind. Context builds go through the existing table agent."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.base import AssetAgent, AssetAgentProvider
from intelegent_data_catalog.assets.models import AssetAnalysisContext, AssetContextOptions
from intelegent_data_catalog.assets.strategies.flags import TABLE_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError, PersistenceUnavailableError
from intelegent_data_catalog.knowledge.models import TableAnalysisContext, TableContextOptions
from intelegent_data_catalog.knowledge.operations.table_operation import TableAgent
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AssetKind
from intelegent_data_catalog.persistence.models import AssetRow, CatalogTableRow
from intelegent_data_catalog.registry.reads import dump_model

_JSON_OBJECT: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])


class TableAssetDetail(BaseModel):
    """Catalog table behind a table asset."""

    model_config = ConfigDict(extra="forbid")

    table_id: UUID
    source_key: str
    table_format: str | None


class TableAssetAgentAdapter(AssetAgent):
    """Maps asset build and describe calls onto ``TableAgent``."""

    def __init__(
        self,
        table_id: UUID,
        asset_id: UUID,
        agent: TableAgent,
        session: Session,
        *,
        save_table: bool,
    ) -> None:
        self._table_id: UUID = table_id
        self._asset_id: UUID = asset_id
        self._agent: TableAgent = agent
        self._session: Session = session
        self._save_table: bool = save_table

    def get_metadata(self) -> BaseModel:
        """Load the table catalog context."""
        return self._agent.get_metadata()

    def build_context(self, options: AssetContextOptions) -> AssetAnalysisContext:
        """Build the table snapshot and store it on the asset."""
        flags: dict[str, bool] = options.flags
        built: TableAnalysisContext = self._agent.build_context(
            TableContextOptions(
                include_data_profile=flags.get(
                    "include_data_profile",
                    TABLE_FLAGS["include_data_profile"],
                ),
                include_graph=flags.get("include_graph", TABLE_FLAGS["include_graph"]),
                include_lineage=flags.get("include_lineage", TABLE_FLAGS["include_lineage"]),
                model=options.model,
                effort=options.effort,
            ),
        )
        summary: str | None = built.table.description or built.table.name
        return AssetAnalysisContext(
            asset_id=self._asset_id,
            kind=AssetKind.TABLE,
            summary=summary,
            built_at=utc_now(),
            payload=_analysis_payload(built),
        )

    def describe(self) -> str:
        """Write business context. An alias keeps the shared table context."""
        if self._save_table:
            return self._agent.describe_table()
        return self._agent.propose_table_context()


class TableStrategy:
    """Catalog table addressed through one asset id."""

    def default_flags(self) -> dict[str, bool]:
        """Flags for a table context build."""
        return dict(TABLE_FLAGS)

    def agent(
        self,
        session: Session,
        agents: AssetAgentProvider,
        asset_id: UUID,
    ) -> AssetAgent:
        """Adapter over the table agent for this asset's table."""
        table: CatalogTableRow = _table(session, asset_id)
        primary: bool = table.asset_id == asset_id
        return agents.for_table(table.id, asset_id, primary=primary)

    def detail(self, session: Session, asset_id: UUID) -> TableAssetDetail:
        """Table id and source key."""
        table: CatalogTableRow = _table(session, asset_id)
        return TableAssetDetail(
            table_id=table.id,
            source_key=table.source_key,
            table_format=table.table_format,
        )


def _table(session: Session, asset_id: UUID) -> CatalogTableRow:
    asset: AssetRow | None = session.get(AssetRow, asset_id)
    linked: CatalogTableRow | None = _linked_table(session, asset)
    if linked is not None:
        return linked
    table: CatalogTableRow | None = session.scalar(
        select(CatalogTableRow).where(CatalogTableRow.asset_id == asset_id),
    )
    if table is None:
        raise EntityNotFoundError("catalog table", str(asset_id))
    return table


def _linked_table(session: Session, asset: AssetRow | None) -> CatalogTableRow | None:
    """Table named by ``properties.table_id``. Missing or stale ids fall through."""
    if asset is None:
        return None
    raw: object = asset.properties.get("table_id")
    if not isinstance(raw, str):
        return None
    try:
        table_id: UUID = UUID(raw)
    except ValueError:
        return None
    return session.get(CatalogTableRow, table_id)


def _analysis_payload(built: TableAnalysisContext) -> dict[str, JsonValue]:
    """JSON object stored on the asset for one table snapshot."""
    raw: dict[str, object] = dump_model(built)
    try:
        payload: dict[str, JsonValue] = _JSON_OBJECT.validate_python(raw)
    except ValidationError as err:
        raise PersistenceUnavailableError("stored catalog JSON is invalid") from err
    return payload
