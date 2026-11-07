"""Schema kind. One agent holds one context for every matching table."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from intelegent_data_catalog.agents.base import ContextBuilder, ContextBuilderProvider
from intelegent_data_catalog.agents.models import AgentAnalysisContext, AgentContextOptions
from intelegent_data_catalog.agents.strategies.flags import TABLE_FLAGS
from intelegent_data_catalog.exceptions import EntityNotFoundError, LlmUnavailableError
from intelegent_data_catalog.knowledge.models import (
    SchemaCatalogContext,
    SchemaColumnFact,
    SchemaContextProposal,
    SchemaTableFact,
)
from intelegent_data_catalog.llm.agent import ProposalAgent
from intelegent_data_catalog.persistence.clock import utc_now
from intelegent_data_catalog.persistence.enums import AgentKind
from intelegent_data_catalog.persistence.models import (
    AgentRow,
    AirflowDagRow,
    AirflowDagTableRow,
    CatalogTableRow,
    ColumnRow,
    RelationshipRow,
)


class SchemaAgentDetail(BaseModel):
    """Catalog, namespace, and the tables the schema agent covers right now."""

    model_config = ConfigDict(extra="forbid")

    catalog: str
    namespace: str
    data_source: str | None
    tables: list[str]


class SchemaContextBuilder(ContextBuilder):
    """Builds one snapshot and one description for a schema agent."""

    def __init__(
        self,
        session: Session,
        agent_id: UUID,
        describer: ProposalAgent[SchemaCatalogContext, SchemaContextProposal] | None,
    ) -> None:
        self._session: Session = session
        self._agent_id: UUID = agent_id
        self._describer: ProposalAgent[SchemaCatalogContext, SchemaContextProposal] | None = (
            describer
        )

    def get_metadata(self) -> SchemaCatalogContext:
        """Load the schema facts with every table flag on."""
        return _snapshot(self._session, self._agent_id, dict(TABLE_FLAGS))

    def build_context(self, options: AgentContextOptions) -> AgentAnalysisContext:
        """Store one snapshot of every matching table."""
        loaded: SchemaCatalogContext = _snapshot(self._session, self._agent_id, options.flags)
        summary: str = f"{loaded.catalog}.{loaded.namespace}"
        payload: dict[str, JsonValue] = loaded.model_dump(mode="json")
        return AgentAnalysisContext(
            agent_id=self._agent_id,
            kind=AgentKind.SCHEMA,
            summary=summary,
            built_at=utc_now(),
            payload=payload,
        )

    def describe(self) -> str:
        """Ask the model once and return the schema context. Tables are not updated."""
        if self._describer is None:
            raise LlmUnavailableError("context generation is not configured")
        loaded: SchemaCatalogContext = _snapshot(self._session, self._agent_id, dict(TABLE_FLAGS))
        proposed: SchemaContextProposal = self._describer.propose(loaded)
        return proposed.context


class SchemaStrategy:
    """One schema declaration addressed through one agent id."""

    def default_flags(self) -> dict[str, bool]:
        """Flags for a schema context build."""
        return dict(TABLE_FLAGS)

    def builder(
        self,
        session: Session,
        agents: ContextBuilderProvider,
        agent_id: UUID,
    ) -> ContextBuilder:
        """Adapter that reads every matching table for this agent."""
        return agents.for_schema(agent_id)

    def detail(self, session: Session, agent_id: UUID) -> SchemaAgentDetail:
        """Catalog, namespace, data source, and current table names."""
        agent: AgentRow = _agent(session, agent_id)
        tables: list[CatalogTableRow] = _tables(session, agent, {})
        data_source: object = agent.properties.get("data_source")
        return SchemaAgentDetail(
            catalog=_text(agent.properties.get("catalog")),
            namespace=_text(agent.properties.get("namespace")),
            data_source=data_source if isinstance(data_source, str) else None,
            tables=[table.name for table in tables],
        )


def _snapshot(session: Session, agent_id: UUID, flags: dict[str, bool]) -> SchemaCatalogContext:
    agent: AgentRow = _agent(session, agent_id)
    include_graph: bool = flags.get("include_graph", TABLE_FLAGS["include_graph"])
    include_lineage: bool = flags.get("include_lineage", TABLE_FLAGS["include_lineage"])
    include_profile: bool = flags.get("include_data_profile", TABLE_FLAGS["include_data_profile"])
    rows: list[CatalogTableRow] = _tables(session, agent, flags)
    table_ids: list[UUID] = [row.id for row in rows]
    columns: dict[UUID, list[SchemaColumnFact]] = _columns(session, table_ids)
    neighbors: dict[UUID, list[str]] = _neighbors(session, rows) if include_graph else {}
    lineage: dict[UUID, list[str]] = _lineage(session, table_ids) if include_lineage else {}
    facts: list[SchemaTableFact] = []
    row: CatalogTableRow
    for row in rows:
        facts.append(
            SchemaTableFact(
                name=row.name,
                source_key=row.source_key,
                description=row.description,
                columns=columns.get(row.id, []),
                neighbors=neighbors.get(row.id, []),
                lineage=lineage.get(row.id, []),
                data_profile="not collected" if include_profile else None,
            ),
        )
    context: str | None = agent.context
    existing: str | None = context if context is not None and context.strip() != "" else None
    return SchemaCatalogContext(
        agent_id=agent.id,
        catalog=_text(agent.properties.get("catalog")),
        namespace=_text(agent.properties.get("namespace")),
        tables=facts,
        existing_context=existing,
    )


def _agent(session: Session, agent_id: UUID) -> AgentRow:
    agent: AgentRow | None = session.get(AgentRow, agent_id)
    if agent is None:
        raise EntityNotFoundError("agent", str(agent_id))
    return agent


def _tables(session: Session, agent: AgentRow, flags: dict[str, bool]) -> list[CatalogTableRow]:
    del flags
    catalog: str = _text(agent.properties.get("catalog"))
    namespace: str = _text(agent.properties.get("namespace"))
    statement = select(CatalogTableRow).where(
        CatalogTableRow.physical_catalog == catalog,
        CatalogTableRow.physical_namespace == namespace,
    )
    include: object = agent.properties.get("include")
    exclude: object = agent.properties.get("exclude")
    if isinstance(include, list) and len(include) > 0:
        names: list[str] = [item for item in include if isinstance(item, str)]
        statement = statement.where(CatalogTableRow.name.in_(names))
    if isinstance(exclude, list) and len(exclude) > 0:
        blocked: list[str] = [item for item in exclude if isinstance(item, str)]
        statement = statement.where(CatalogTableRow.name.notin_(blocked))
    return list(session.scalars(statement.order_by(CatalogTableRow.name)))


def _columns(session: Session, table_ids: list[UUID]) -> dict[UUID, list[SchemaColumnFact]]:
    found: dict[UUID, list[SchemaColumnFact]] = {}
    if len(table_ids) == 0:
        return found
    column: ColumnRow
    for column in session.scalars(
        select(ColumnRow)
        .where(ColumnRow.table_id.in_(table_ids))
        .order_by(ColumnRow.ordinal_position),
    ):
        bucket: list[SchemaColumnFact] = found.setdefault(column.table_id, [])
        bucket.append(SchemaColumnFact(name=column.name, data_type=column.data_type))
    return found


def _neighbors(session: Session, tables: list[CatalogTableRow]) -> dict[UUID, list[str]]:
    names: dict[UUID, str] = {table.id: table.name for table in tables}
    ids: list[UUID] = list(names)
    found: dict[UUID, list[str]] = {table_id: [] for table_id in ids}
    if len(ids) == 0:
        return found
    link: RelationshipRow
    for link in session.scalars(
        select(RelationshipRow).where(
            or_(
                RelationshipRow.source_table_id.in_(ids),
                RelationshipRow.target_table_id.in_(ids),
            ),
        ),
    ):
        source_name: str | None = names.get(link.source_table_id)
        target_name: str | None = names.get(link.target_table_id)
        if (
            source_name is not None
            and target_name is not None
            and target_name not in found[link.source_table_id]
        ):
            found[link.source_table_id].append(target_name)
        if (
            target_name is not None
            and source_name is not None
            and source_name not in found[link.target_table_id]
        ):
            found[link.target_table_id].append(source_name)
    return found


def _lineage(session: Session, table_ids: list[UUID]) -> dict[UUID, list[str]]:
    found: dict[UUID, list[str]] = {table_id: [] for table_id in table_ids}
    if len(table_ids) == 0:
        return found
    row: tuple[UUID, str]
    for row in session.execute(
        select(AirflowDagTableRow.catalog_table_id, AirflowDagRow.dag_id)
        .join(AirflowDagRow, AirflowDagRow.id == AirflowDagTableRow.dag_row_id)
        .where(AirflowDagTableRow.catalog_table_id.in_(table_ids)),
    ):
        table_id: UUID = row[0]
        dag_id: str = row[1]
        if dag_id not in found[table_id]:
            found[table_id].append(dag_id)
    return found


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""
