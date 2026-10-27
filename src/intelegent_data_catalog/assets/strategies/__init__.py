"""Kind strategies. One object per ``AssetKind``."""

from intelegent_data_catalog.assets.strategies.dag import DagStrategy
from intelegent_data_catalog.assets.strategies.file import FileStrategy
from intelegent_data_catalog.assets.strategies.instance import InstanceStrategy
from intelegent_data_catalog.assets.strategies.protocol import KindStrategy
from intelegent_data_catalog.assets.strategies.table import TableStrategy

__all__ = [
    "DagStrategy",
    "FileStrategy",
    "InstanceStrategy",
    "KindStrategy",
    "TableStrategy",
]
