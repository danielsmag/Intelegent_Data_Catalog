"""Steps an agent runs. Each operator is one task in the run."""

from __future__ import annotations

from typing import ClassVar

from intelegent_data_catalog.agents.dsl.operators.base import (
    Operator,
    OperatorContext,
    OperatorObserver,
    _run_step,
)
from intelegent_data_catalog.agents.execution.tasks import BUILD_CONTEXT, DESCRIBE

_FLAG_NAMES: tuple[str, ...] = (
    "include_graph",
    "include_lineage",
    "include_data_profile",
    "include_source",
    "include_previous",
    "include_outputs",
    "include_table_links",
    "use_rules",
    "use_skills",
)


class BuildContext(Operator):
    """Build the agent snapshot. Only flags the file sets are stored and applied."""

    task_id: ClassVar[str] = BUILD_CONTEXT
    include_graph: bool | None = None
    include_lineage: bool | None = None
    include_data_profile: bool | None = None
    include_source: bool | None = None
    include_previous: bool | None = None
    include_outputs: bool | None = None
    include_table_links: bool | None = None
    use_rules: bool | None = None
    use_skills: bool | None = None

    def context_flags(self) -> dict[str, bool]:
        """Flags this step was given. Omitted fields stay out."""
        flags: dict[str, bool] = {}
        name: str
        for name in _FLAG_NAMES:
            if name not in self.model_fields_set:
                continue
            value: object = getattr(self, name)
            if isinstance(value, bool):
                flags[name] = value
        return flags

    def set_flag_names(self) -> frozenset[str]:
        """Flag names present on this step, whether or not the source allows them."""
        return frozenset(name for name in _FLAG_NAMES if name in self.model_fields_set)

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Build the snapshot with the flags this step set."""
        flags: dict[str, bool] = self.context_flags()

        def build() -> None:
            context.build_context(flags)

        _run_step(observer, self.task_id, build)


class Describe(Operator):
    """Write business context. ``skip_described`` leaves an existing context alone."""

    task_id: ClassVar[str] = DESCRIBE
    skip_described: bool = False

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Ask the model unless this step skips an agent that already has context."""

        def describe() -> None:
            if self.skip_described and context.has_context():
                return
            context.describe()

        _run_step(observer, self.task_id, describe)
