"""Asset search accepts every rank key and hides another user's personal files."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy.orm import Session

from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import (
    AssetKind,
    AssetStatus,
    CatalogJobKind,
    CatalogJobStatus,
    DataSourceType,
    TableKind,
    TableStatus,
)
from intelegent_data_catalog.persistence.models import (
    AssetRow,
    CatalogJobRow,
    CatalogTableRow,
    DataSourceRow,
)
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
            assert isinstance(row["interval_seconds"], int)
            assert isinstance(row["membership_count"], int)
            assert row["context_state"] in {"none", "user", "llm"}
    finally:
        database.dispose()


def test_search_includes_a_running_table_build(tmp_path: Path) -> None:
    """A knowledge build on the asset's table does not break the asset list."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    session: Session = database.session()
    try:
        now: datetime = datetime.now(UTC)
        asset_id = uuid4()
        source_id = uuid4()
        table_id = uuid4()
        session.add(
            AssetRow(
                id=asset_id,
                kind=AssetKind.TABLE.value,
                name="orders",
                slug="orders",
                description=None,
                context=None,
                context_origin=None,
                workspace_id=SHARED_WORKSPACE_ID,
                owner_user_id=None,
                created_by=None,
                properties={"source_key": "db.public.orders"},
                status=AssetStatus.ACTIVE.value,
                last_synced_at=None,
                created_at=now,
                updated_at=now,
            ),
        )
        session.add(
            DataSourceRow(
                id=source_id,
                name="pg",
                type=DataSourceType.POSTGRES.value,
                configuration={},
                created_at=now,
                updated_at=now,
            ),
        )
        session.flush()
        session.add(
            CatalogTableRow(
                id=table_id,
                asset_id=asset_id,
                data_source_id=source_id,
                name="orders",
                table_type=TableKind.TABLE.value,
                status=TableStatus.ACTIVE.value,
                physical_catalog="db",
                physical_namespace="public",
                physical_table="orders",
                source_ref={},
                source_key="db.public.orders",
                created_at=now,
                updated_at=now,
            ),
        )
        session.flush()
        session.add(
            CatalogJobRow(
                id=uuid4(),
                kind=CatalogJobKind.KNOWLEDGE.value,
                subject_id=table_id,
                subject_name="orders",
                lane="",
                status=CatalogJobStatus.RUNNING.value,
                error=None,
                options={},
                created_at=now,
                finished_at=None,
            ),
        )
        session.commit()
        found: httpx.Response = _send(app, "GET", "/api/v1/assets?limit=10")
        assert found.status_code == 200
        body: dict[str, object] = found.json()
        assert body["total"] == 1
        items: object = body["items"]
        assert isinstance(items, list)
        row: object = items[0]
        assert isinstance(row, dict)
        assert row["name"] == "orders"
        assert row["build_running"] is True
    finally:
        session.close()
        database.dispose()
