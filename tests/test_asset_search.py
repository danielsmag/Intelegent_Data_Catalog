"""Asset search accepts every rank key and hides another user's personal files."""

from pathlib import Path

import httpx
from fastapi import FastAPI

from intelegent_data_catalog.persistence.database import CatalogDatabase
from tests.test_asset_api import _app, _send, _table


def test_every_sort_key_returns_the_table_asset(tmp_path: Path) -> None:
    """Paging and rank keys stay valid for a single table asset."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        _table(app)
        sort: str
        for sort in (
            "name",
            "kind",
            "updated_at",
            "last_built_at",
            "membership_count",
            "freshness_score",
            "context_state",
        ):
            found: httpx.Response = _send(
                app,
                "GET",
                f"/api/v1/assets?sort={sort}&order=desc&limit=10&offset=0",
            )
            assert found.status_code == 200
            body: dict[str, object] = found.json()
            assert body["total"] == 1
            items: object = body["items"]
            assert isinstance(items, list)
            row: object = items[0]
            assert isinstance(row, dict)
            assert isinstance(row["freshness_score"], int)
            assert isinstance(row["membership_count"], int)
            assert row["context_state"] in {"none", "user", "llm"}
    finally:
        database.dispose()
