"""Knowledge Builder foundation.

A table agent is created for one catalog id, loads context, and can ask the
configured language model. Persistent knowledge stays in PostgreSQL. This
package does not write to Neo4j.
"""

from intelegent_data_catalog.knowledge.agents.table_agent import TableAgent
from intelegent_data_catalog.knowledge.context.builder import TableContextBuilder
from intelegent_data_catalog.knowledge.context.catalog import (
    CatalogContextProvider,
    PostgresCatalogContextProvider,
)
from intelegent_data_catalog.knowledge.context.graph import (
    GraphContextProvider,
    ProjectionGraphContextProvider,
)
from intelegent_data_catalog.knowledge.context.lineage import (
    LineageContextProvider,
    MarquezLineageContextProvider,
)
from intelegent_data_catalog.knowledge.factories.table_agent_factory import (
    TableAgentFactory,
    build_table_agent_factory,
)
from intelegent_data_catalog.knowledge.models import (
    ColumnComparisonProfile,
    ColumnDataProfile,
    ColumnMetadata,
    DataAccessMode,
    DataProfileOptions,
    KnowledgeDefaults,
    LineageContext,
    ProfileOverrides,
    TableAnalysisContext,
    TableCatalogContext,
    TableDataProfile,
    TableMetadata,
)
from intelegent_data_catalog.knowledge.profiling.profiler import DataProfiler

__all__ = [
    "CatalogContextProvider",
    "ColumnComparisonProfile",
    "ColumnDataProfile",
    "ColumnMetadata",
    "DataAccessMode",
    "DataProfileOptions",
    "DataProfiler",
    "GraphContextProvider",
    "KnowledgeDefaults",
    "LineageContext",
    "LineageContextProvider",
    "MarquezLineageContextProvider",
    "PostgresCatalogContextProvider",
    "ProfileOverrides",
    "ProjectionGraphContextProvider",
    "TableAgent",
    "TableAgentFactory",
    "TableAnalysisContext",
    "TableCatalogContext",
    "TableContextBuilder",
    "TableDataProfile",
    "TableMetadata",
    "build_table_agent_factory",
]
