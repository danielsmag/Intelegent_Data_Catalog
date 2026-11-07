"""Asset notebooks: object-store compare-and-swap, overlay, and the read API."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from pydantic_ai import Agent
from pydantic_ai_harness.memory._store import (
    MemoryConflictError,
    MemoryFile,
    MemoryMutation,
    MemoryOperation,
    MemoryOperationConflictError,
    MemoryPathEscapeError,
    MemorySearchResult,
)
from sqlalchemy.orm import Session

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.assets.agent_pack.memory_store import ObjectMemoryStore
from intelegent_data_catalog.assets.agent_pack.models import MemoryFileDetail, MemoryFileRead
from intelegent_data_catalog.assets.agent_pack.service import AgentPackService
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.llm.overlay import DescribeOverlay, current_memory, describe_overlay
from intelegent_data_catalog.llm.run import ModelCall
from intelegent_data_catalog.persistence.constants import SHARED_WORKSPACE_ID
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.enums import AssetKind, AssetStatus
from intelegent_data_catalog.persistence.migrate import upgrade_database
from intelegent_data_catalog.persistence.models import AssetRow
from intelegent_data_catalog.storage.object_store import MemoryObjectStore


class _Stop(Exception):
    """Stops a model call after it records the run arguments."""


class _StubAgent:
    """Records ``run`` arguments and then stops."""

    def __init__(self) -> None:
        self.model: object = object()
        self.seen: dict[str, object] = {}

    async def run(self, prompt: str, **kwargs: object) -> object:
        self.seen = {"prompt": prompt, **kwargs}
        raise _Stop()


def test_memory_write_rejects_a_stale_etag() -> None:
    store: ObjectMemoryStore = ObjectMemoryStore(MemoryObjectStore(), "agent-memory/")
    saved: MemoryMutation = asyncio.run(
        store.write_with_metadata(
            "asset/main/MEMORY.md",
            "orders are sales",
            expected_version=None,
            metadata={"updated_by": "agent"},
        ),
    )
    with pytest.raises(MemoryConflictError):
        asyncio.run(
            store.write(
                "asset/main/MEMORY.md",
                "other",
                expected_version="stale",
            ),
        )
    current: MemoryFile | None = asyncio.run(store.read("asset/main/MEMORY.md", max_chars=100))
    assert current is not None
    assert current.version == saved.version
    assert current.content == "orders are sales"
    assert current.metadata == {"updated_by": "agent"}


def test_replay_returns_the_first_write_and_hides_the_receipt() -> None:
    store: ObjectMemoryStore = ObjectMemoryStore(MemoryObjectStore(), "agent-memory/")
    operation: MemoryOperation = MemoryOperation(id="a" * 64, fingerprint="b" * 64)
    saved: MemoryMutation = asyncio.run(
        store.write(
            "asset/main/MEMORY.md",
            "orders are sales",
            expected_version=None,
            operation=operation,
        ),
    )
    replayed: MemoryMutation = asyncio.run(
        store.write(
            "asset/main/MEMORY.md",
            "other",
            expected_version=None,
            operation=operation,
        ),
    )
    assert replayed.replayed is True
    assert replayed.version == saved.version
    current: MemoryFile | None = asyncio.run(store.read("asset/main/MEMORY.md", max_chars=100))
    assert current is not None
    assert current.content == "orders are sales"
    paths: list[str] = asyncio.run(store.list_paths("", limit=20))
    assert paths == ["asset/main/MEMORY.md"]
    with pytest.raises(MemoryOperationConflictError):
        asyncio.run(
            store.get_operation(MemoryOperation(id="a" * 64, fingerprint="c" * 64)),
        )


def test_search_finds_a_note_and_rejects_a_path_escape() -> None:
    store: ObjectMemoryStore = ObjectMemoryStore(MemoryObjectStore(), "agent-memory/")
    asyncio.run(
        store.write(
            "asset/main/keys.md",
            "order_id is the business key",
            expected_version=None,
        ),
    )
    found: MemorySearchResult = asyncio.run(
        store.search(
            "asset/main/",
            "business",
            limit=10,
            max_files=10,
            max_chars=1_000,
            max_file_chars=1_000,
        ),
    )
    assert len(found.matches) == 1
    assert found.matches[0].path == "asset/main/keys.md"
    with pytest.raises(MemoryPathEscapeError):
        asyncio.run(store.read("../secret.md", max_chars=10))


def test_asset_overlay_attaches_memory_and_a_plain_call_does_not() -> None:
    objects: MemoryObjectStore = MemoryObjectStore()
    asset_id: UUID = uuid4()
    overlay: DescribeOverlay = DescribeOverlay(None, None, asset_id, objects)
    with describe_overlay(overlay):
        attached = current_memory()
    assert attached is not None
    assert attached.namespace == str(asset_id)
    assert attached.scanner is not None
    assert attached.write_metadata == {"updated_by": "agent"}
    assert current_memory() is None

    bare: _StubAgent = _StubAgent()
    call: ModelCall[object, str] = ModelCall(
        cast(Agent[object, str], bare),
        name="memory-test",
        max_requests=2,
        temperature=0.0,
        max_tokens=16,
        timeout_seconds=5.0,
    )
    with describe_overlay(overlay), pytest.raises(_Stop):
        call.run("describe the asset", object())
    capabilities: object = bare.seen["capabilities"]
    assert isinstance(capabilities, list)
    assert len(capabilities) == 1
    assert capabilities[0].namespace == str(asset_id)
    limits: object = bare.seen["usage_limits"]
    assert getattr(limits, "request_limit", None) == 4

    with pytest.raises(_Stop):
        call.run("describe the scope", object())
    assert bare.seen["capabilities"] is None
    limits = bare.seen["usage_limits"]
    assert getattr(limits, "request_limit", None) == 2


def test_api_lists_and_reads_a_note(tmp_path: Path) -> None:
    app: FastAPI
    database: CatalogDatabase
    app, database, objects = _app(tmp_path)
    session: Session = database.session()
    try:
        asset_id: UUID = uuid4()
        _asset(session, asset_id, owner_user_id=None)
        session.commit()
        asyncio.run(
            ObjectMemoryStore(objects, "agent-memory/").write_with_metadata(
                f"{asset_id}/main/MEMORY.md",
                "orders are sales",
                expected_version=None,
                metadata={"updated_by": "agent"},
            ),
        )
        spec: dict[str, object] = app.openapi()
        paths: object = spec["paths"]
        assert isinstance(paths, dict)
        listing: object = paths["/api/v1/assets/{asset_id}/agent-memory"]
        detail: object = paths["/api/v1/assets/{asset_id}/agent-memory/{name}"]
        assert isinstance(listing, dict)
        assert isinstance(detail, dict)
        assert set(listing) == {"get"}
        assert set(detail) == {"get"}
        listed: httpx.Response = _send(app, "GET", f"/api/v1/assets/{asset_id}/agent-memory")
        assert listed.status_code == 200
        body: object = listed.json()
        assert isinstance(body, list)
        assert body[0]["name"] == "MEMORY.md"
        detail_response: httpx.Response = _send(
            app,
            "GET",
            f"/api/v1/assets/{asset_id}/agent-memory/MEMORY.md",
        )
        assert detail_response.status_code == 200
        note: dict[str, object] = detail_response.json()
        assert note["body"] == "orders are sales"
        assert note["updated_by"] == "agent"
    finally:
        session.close()
        database.dispose()


def test_hidden_asset_memory_is_not_found(tmp_path: Path) -> None:
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    try:
        asset_id: UUID = uuid4()
        _asset(session, asset_id, owner_user_id=None)
        session.commit()
        objects: MemoryObjectStore = MemoryObjectStore()
        asyncio.run(
            ObjectMemoryStore(objects, "agent-memory/").write(
                f"{asset_id}/main/MEMORY.md",
                "private",
                expected_version=None,
            ),
        )
        service: AgentPackService = _service(
            session,
            objects,
            admin=False,
            user_id=uuid4(),
            members=frozenset(),
        )
        with pytest.raises(EntityNotFoundError):
            service.list_memory(asset_id)
        with pytest.raises(ValidationAppError):
            _service(session, objects, admin=True, user_id=uuid4()).get_memory(asset_id, "nope")
        visible: list[MemoryFileRead] = _service(
            session,
            objects,
            admin=True,
            user_id=uuid4(),
        ).list_memory(asset_id)
        assert [item.name for item in visible] == ["MEMORY.md"]
        detail: MemoryFileDetail = _service(
            session,
            objects,
            admin=True,
            user_id=uuid4(),
        ).get_memory(asset_id, "MEMORY.md")
        assert detail.body == "private"
    finally:
        session.close()
        database.dispose()


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase, MemoryObjectStore]:
    database: CatalogDatabase
    session: Session
    database, session = _session(tmp_path)
    session.close()
    objects: MemoryObjectStore = MemoryObjectStore()
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    container.asset_definition_store.override(providers.Object(objects))
    return create_app(container), database, objects


def _session(tmp_path: Path) -> tuple[CatalogDatabase, Session]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    return database, database.session()


def _asset(session: Session, asset_id: UUID, owner_user_id: UUID | None) -> None:
    now: datetime = datetime.now(UTC)
    session.add(
        AssetRow(
            id=asset_id,
            kind=AssetKind.TABLE.value,
            name="orders",
            slug="orders",
            workspace_id=SHARED_WORKSPACE_ID,
            owner_user_id=owner_user_id,
            properties={},
            status=AssetStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        ),
    )


def _service(
    session: Session,
    objects: MemoryObjectStore,
    *,
    admin: bool,
    user_id: UUID,
    members: frozenset[UUID] | None = None,
) -> AgentPackService:
    chosen: frozenset[UUID] = (
        frozenset({SHARED_WORKSPACE_ID}) if members is None else members
    )
    access: AccessContext = AccessContext(
        principal=Principal(
            subject="tester",
            email=None,
            name="Tester",
            roles=frozenset(),
            is_admin=admin,
        ),
        user_id=user_id,
        workspace_id=None,
        member_workspace_ids=chosen,
        admin_workspace_ids=frozenset(),
    )
    return AgentPackService(
        session,
        objects,
        AssetDefinitionsSettings(enabled=True, prefix="asset-definitions/"),
        access,
    )


def _send(app: FastAPI, method: str, path: str) -> httpx.Response:
    return asyncio.run(_request(app, method, path))


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path)
