"""Errors raised while a definition file is collected."""

from intelegent_data_catalog.exceptions import CatalogError


class AssetDefinitionError(CatalogError):
    """Raised when a definition file breaks a DSL rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AssetDefinitionImportError(CatalogError):
    """Raised when executing a definition file fails. The traceback is kept for the scan row."""

    def __init__(self, message: str, traceback_text: str) -> None:
        self.message: str = message
        self.traceback_text: str = traceback_text
        super().__init__(message)
