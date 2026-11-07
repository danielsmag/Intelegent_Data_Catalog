"""Errors raised while an asset definition file is collected."""

from intelegent_data_catalog.exceptions import CatalogError


class AssetDefinitionError(CatalogError):
    """Raised when a definition file breaks an asset DSL rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AssetDefinitionImportError(CatalogError):
    """Raised when executing a definition file fails. The traceback is kept on the scan row."""

    def __init__(self, message: str, traceback_text: str) -> None:
        self.message: str = message
        self.traceback_text: str = traceback_text
        super().__init__(message)
