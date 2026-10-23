"""Operator contract. A definition file names a source; it does not load it."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from intelegent_data_catalog.assets.definitions.models import SourceRef


class Operator(BaseModel, ABC):
    """Names the catalog source behind an asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @abstractmethod
    def to_source(self) -> SourceRef:
        """Return the stored source reference."""
