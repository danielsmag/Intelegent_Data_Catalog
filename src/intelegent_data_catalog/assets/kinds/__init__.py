"""Asset kind classes."""

from intelegent_data_catalog.assets.kinds.dag_asset import DagAsset
from intelegent_data_catalog.assets.kinds.file_asset import FileAsset, NotebookAsset
from intelegent_data_catalog.assets.kinds.table_asset import TableAsset

__all__ = ["DagAsset", "FileAsset", "NotebookAsset", "TableAsset"]
