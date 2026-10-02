"""Spark SQL preview for a catalog table. Trino addresses are never substituted."""

import re

from intelegent_data_catalog.persistence.enums import BindingRole
from intelegent_data_catalog.registry.schemas import AssetBindingRead, EngineRef

NOT_CONFIGURED: str = "Not configured for Spark queries"
_IDENT: re.Pattern[str] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SparkQueryPreview:
    """Whether a catalog table can open in the Spark editor."""

    def __init__(
        self,
        available: bool,
        message: str | None,
        sql: str | None,
        engine_ref_key: str | None,
    ) -> None:
        self.available: bool = available
        self.message: str | None = message
        self.sql: str | None = sql
        self.engine_ref_key: str | None = engine_ref_key


def spark_preview(bindings: list[AssetBindingRead], limit: int) -> SparkQueryPreview:
    """Use the selected QUERY binding. Missing bindings stay unavailable."""
    selected: list[AssetBindingRead] = [
        binding for binding in bindings if binding.role is BindingRole.QUERY and binding.is_selected
    ]
    if not selected:
        return SparkQueryPreview(
            available=False,
            message=NOT_CONFIGURED,
            sql=None,
            engine_ref_key=None,
        )
    binding: AssetBindingRead = selected[0]
    ref: EngineRef = binding.engine_ref
    sql: str = (
        "SELECT * FROM "
        f"{_quote(ref.catalog)}.{_quote(ref.namespace)}.{_quote(ref.table)} "
        f"LIMIT {limit}"
    )
    return SparkQueryPreview(
        available=True,
        message=None,
        sql=sql,
        engine_ref_key=binding.engine_ref_key,
    )


def _quote(identifier: str) -> str:
    if _IDENT.fullmatch(identifier):
        return identifier
    escaped: str = identifier.replace("`", "``")
    return f"`{escaped}`"
