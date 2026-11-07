"""Kind strategies. One object per ``AgentKind``."""

from intelegent_data_catalog.agents.strategies.dag import DagStrategy
from intelegent_data_catalog.agents.strategies.file import FileStrategy
from intelegent_data_catalog.agents.strategies.instance import InstanceStrategy
from intelegent_data_catalog.agents.strategies.protocol import KindStrategy
from intelegent_data_catalog.agents.strategies.table import TableStrategy

__all__ = [
    "DagStrategy",
    "FileStrategy",
    "InstanceStrategy",
    "KindStrategy",
    "TableStrategy",
]
