"""Contracts for catalog model agents. Storage stays outside these types."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from intelegent_data_catalog.knowledge.models import (
        ColumnContextInput,
        ColumnContextProposal,
        TableColumnsContextInput,
        TableColumnsContextProposal,
        TableContextInput,
        TableContextProposal,
    )
    from intelegent_data_catalog.llm.tools.table import TableToolSource


class ProposalAgent[ContextT, ProposalT](Protocol):
    """One structured model call. Context in, proposal out."""

    def propose(self, context: ContextT) -> ProposalT:
        """Return the proposal selected from ``context``."""
        ...


class TableDescriber(Protocol):
    """Writes business context for a table and its columns. Implementations call a model."""

    def describe_table(self, context: TableContextInput) -> TableContextProposal:
        """Return context for the table."""
        ...

    def describe_columns(self, context: TableColumnsContextInput) -> TableColumnsContextProposal:
        """Return context for every column in ``context.columns``."""
        ...

    def describe_column(self, context: ColumnContextInput) -> ColumnContextProposal:
        """Return context for ``context.column``."""
        ...


class TableModelAgent(TableDescriber, Protocol):
    """The model behind one table: a tool-using operation plus context writing."""

    def run(self, instruction: str, source: TableToolSource, instructions: str) -> str:
        """Run until the operation finishes or the request limit is reached."""
        ...
