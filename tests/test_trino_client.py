"""Trino driver rows are lists. Discovery must still read them."""

from collections.abc import Sequence

from intelegent_data_catalog.sources.trino.client import _materialize_rows


class _Column:
    def __init__(self, name: str) -> None:
        self.name: str = name


def test_materialize_rows_accepts_trino_lists() -> None:
    """SHOW CATALOGS returns a list of lists, not tuples."""
    description: Sequence[object] = [_Column("Catalog")]
    rows: list[dict[str, object]] = _materialize_rows(description, [["polaris"], ["system"]])
    assert rows == [{"catalog": "polaris"}, {"catalog": "system"}]
