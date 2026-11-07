"""Schema operator. One declaration fans out to one table asset per catalog table."""

from __future__ import annotations

from pydantic import Field, field_validator

from intelegent_data_catalog.assets.agent_pack.compose import explicit_pack_flags
from intelegent_data_catalog.assets.definitions.models import SchemaSourceRef
from intelegent_data_catalog.assets.dsl.operators.base import (
    OperatorContext,
    OperatorObserver,
    _run_step,
)
from intelegent_data_catalog.assets.dsl.operators.tables.base import TableContextOperator
from intelegent_data_catalog.assets.execution.tasks import BUILD_CONTEXT, DESCRIBE


class SchemaOperator(TableContextOperator):
    """Link every table in one catalog namespace.

    ``describe`` turns the model call off. ``skip_described`` skips tables that
    already have a context. ``include`` and ``exclude`` name tables. ``pack_*``
    limit the agent files stored beside each table.
    """

    catalog: str = Field(min_length=1, max_length=512)
    namespace: str = Field(min_length=1, max_length=512)
    data_source: str | None = None
    include: list[str] | None = None
    exclude: list[str] = Field(default_factory=list)
    describe: bool = True
    skip_described: bool = True
    pack_max_files: int = Field(default=40, ge=1, le=200)
    pack_single_active_prompt: bool = True

    @field_validator("data_source")
    @classmethod
    def _data_source_not_blank(cls, value: str | None) -> str | None:
        if value is not None and value.strip() == "":
            raise ValueError("data_source cannot be blank")
        return value

    @field_validator("include", "exclude")
    @classmethod
    def _clean_names(cls, value: list[str] | None) -> list[str] | None:
        return _table_names(value)

    def to_source(self) -> SchemaSourceRef:
        """Return the stored source reference, including the table filters."""
        data_source: str | None = None if self.data_source is None else self.data_source.strip()
        return SchemaSourceRef(
            catalog=self.catalog,
            namespace=self.namespace,
            data_source=data_source,
            include=self.include,
            exclude=list(self.exclude),
        )

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Build context, then describe unless this operator skips that step."""
        flags: dict[str, bool] = self.context_flags()

        def build() -> None:
            context.build_context(flags)

        def describe() -> None:
            if not self.describe:
                return
            if self.skip_described and context.has_context():
                return
            context.describe()

        with explicit_pack_flags(flags):
            _run_step(observer, BUILD_CONTEXT, build)
            _run_step(observer, DESCRIBE, describe)


def _table_names(names: list[str] | None) -> list[str] | None:
    """Strip names, reject blanks and duplicates. ``None`` stays ``None``."""
    if names is None:
        return None
    found: list[str] = []
    seen: set[str] = set()
    name: str
    for name in names:
        cleaned: str = name.strip()
        if cleaned == "":
            raise ValueError("table name cannot be blank")
        if cleaned in seen:
            raise ValueError(f"table name is listed twice: {cleaned}")
        seen.add(cleaned)
        found.append(cleaned)
    return found
