"""Agent markdown stored beside an asset declaration."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.agent_pack.compose import (
    LoadedPack,
    LoadedRule,
    LoadedSkill,
    allowed_tools,
    apply_explicit_pack_flags,
    compose_rules,
    explicit_pack_flags,
    narrow_flags,
    scoped_pack,
)
from intelegent_data_catalog.assets.agent_pack.document import (
    MarkdownFile,
    parse_markdown,
    render_markdown,
)
from intelegent_data_catalog.assets.agent_pack.models import PromptWrite, SkillWrite
from intelegent_data_catalog.assets.agent_pack.runtime import operation_capabilities, render_skills
from intelegent_data_catalog.assets.agent_pack.service import AgentPackService
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.entity_configuration import option_values
from intelegent_data_catalog.exceptions import ValidationAppError
from intelegent_data_catalog.persistence.enums import AssetKind, RefreshSubjectKind
from intelegent_data_catalog.persistence.models import AssetDefinitionItemRow, AssetRow
from intelegent_data_catalog.storage.object_store import MemoryObjectStore
from tests import test_asset_definitions as definitions


def test_frontmatter_round_trip() -> None:
    """Tools stay under metadata, and a broken fence is rejected."""
    text: str = render_markdown(
        [
            ("name", "lineage-reader"),
            ("description", "Use upstream tables"),
            ("enabled", True),
            ("metadata", {"tools": ["lineage", "metadata"]}),
        ],
        "Name the upstream table.\n",
    )
    parsed: MarkdownFile = parse_markdown(text)
    assert parsed.text("name") == "lineage-reader"
    assert parsed.flag("enabled", False) is True
    assert parsed.tools() == ["lineage", "metadata"]
    assert parsed.body == "Name the upstream table.\n"
    try:
        parse_markdown("---\nname: broken\n")
    except ValidationAppError as err:
        assert err.field == "body"
    else:
        raise AssertionError("open frontmatter was accepted")


def test_rules_sort_and_profile_stays_off_when_the_flag_is_false() -> None:
    """Enabled rules follow order. A skill cannot turn profiling on."""
    text: str | None = compose_rules(
        [
            LoadedRule("later", "Second.", True, 20),
            LoadedRule("first", "First.", True, 10),
            LoadedRule("off", "Hidden.", False, 0),
        ],
    )
    assert text is not None
    assert text.index("First.") < text.index("Second.")
    assert "Hidden" not in text
    skills: list[LoadedSkill] = [
        LoadedSkill("reader", "Read lineage", "body", True, ["lineage", "profile"]),
    ]
    flags: dict[str, bool] = {"include_lineage": True, "include_data_profile": False}
    allowed: set[str] | None = allowed_tools(skills, flags, AssetKind.TABLE)
    assert allowed == {"lineage"}
    narrowed: dict[str, bool] = narrow_flags(
        {"include_lineage": True, "include_data_profile": False, "include_graph": True},
        allowed,
    )
    assert narrowed["include_graph"] is False
    assert narrowed["include_lineage"] is True


def test_asset_switch_off_skips_rules_and_skills() -> None:
    """A missing switch stays on. Off drops that set. An explicit operator flag wins."""
    values: dict[str, bool] = option_values(
        RefreshSubjectKind.ASSET,
        {"include_graph": False},
        "TABLE",
    )
    assert values["use_rules"] is True
    assert values["use_skills"] is True
    assert values["include_graph"] is False
    pack: LoadedPack = LoadedPack()
    pack.rules = [LoadedRule("keep", "Show this.", True, 0)]
    pack.skills = [LoadedSkill("reader", "Read lineage", "body", True, ["lineage"])]
    assert scoped_pack(pack, values) is pack
    quiet: LoadedPack = scoped_pack(pack, {"use_rules": False, "use_skills": False})
    assert quiet.rules == []
    assert quiet.skills == []
    saved: dict[str, bool] = {"use_rules": True, "use_skills": True}
    with explicit_pack_flags({"use_rules": False, "include_lineage": True}):
        merged: dict[str, bool] = apply_explicit_pack_flags(saved)
    assert merged["use_rules"] is False
    assert merged["use_skills"] is True
    assert "include_lineage" not in merged


def test_private_folder_round_trip_and_flat_file_is_not_writable(tmp_path: Path) -> None:
    """Writes land beside asset_*.py. A shared directory cannot be edited."""
    database, session, store, scanner, _executor = definitions._open(tmp_path)
    try:
        definitions._table(session, "iceberg.sales.orders")
        definitions._put(store, "spaces/space_analytics.py", definitions._SPACE)
        definitions._put(store, "scopes/analytics/scope_finance.py", definitions._SCOPE)
        definitions._put(store, "assets/sales/orders/asset_orders.py", definitions._ORDERS)
        scanner.scan(datetime.now(UTC))
        asset_id: UUID = _asset_id(session)
        service: AgentPackService = _service(session, store)
        service.put_skill(
            asset_id,
            "lineage-reader",
            SkillWrite(
                name="lineage-reader",
                description="Use upstream tables",
                tools=["lineage", "metadata"],
                body="Name the upstream table.\n",
            ),
        )
        service.put_prompt(
            asset_id,
            "finance",
            PromptWrite(
                name="finance",
                description="Finance reader",
                active=True,
                body="Lead with reconcile.\n",
            ),
        )
        try:
            service.put_prompt(
                asset_id,
                "risk",
                PromptWrite(
                    name="risk",
                    description="Risk reader",
                    active=True,
                    body="Lead with risk.\n",
                ),
            )
        except ValidationAppError as err:
            assert err.field == "active"
        else:
            raise AssertionError("a second active prompt was stored")
        listed = service.list_pack(asset_id)
        assert listed.writable is True
        assert listed.prefix == "asset-definitions/assets/sales/orders/"
        assert listed.skills[0].tools == ["lineage", "metadata"]
        key: str = "asset-definitions/assets/sales/orders/.agents/skills/lineage-reader/SKILL.md"
        assert key.encode() != b""
        assert store.get(key).decode().startswith("---\n")
        pack = service.load(asset_id)
        rendered: str = render_skills(store, pack)
        assert "lineage-reader" in rendered
        assert "Name the upstream table." in rendered
        capabilities: list[object] = operation_capabilities(Path("."), ["lineage-reader"])
        assert any(type(item).__name__ == "Skills" for item in capabilities)
        assert any(type(item).__name__ == "RepoContext" for item in capabilities)
    finally:
        session.close()
        database.dispose()


def test_flat_declaration_has_no_private_folder(tmp_path: Path) -> None:
    """assets/sales/asset_orders.py shares its directory, so the pack is read-only."""
    database, session, store, scanner, _executor = definitions._open(tmp_path)
    try:
        definitions._table(session, "iceberg.sales.orders")
        definitions._put(store, "spaces/space_analytics.py", definitions._SPACE)
        definitions._put(store, "scopes/analytics/scope_finance.py", definitions._SCOPE)
        definitions._put(store, "assets/sales/asset_orders.py", definitions._ORDERS)
        definitions._put(store, "assets/sales/asset_customers.py", "x = 1\n")
        scanner.scan(datetime.now(UTC))
        orders: AssetRow | None = session.scalar(select(AssetRow).where(AssetRow.name == "orders"))
        names: list[str] = list(session.scalars(select(AssetRow.name)))
        assert orders is not None, names
        listed = _service(session, store).list_pack(orders.id)
        assert listed.writable is False
        assert listed.skills == []
    finally:
        session.close()
        database.dispose()


def _asset_id(session: Session) -> UUID:
    item: AssetDefinitionItemRow | None = session.scalar(
        select(AssetDefinitionItemRow).where(AssetDefinitionItemRow.item_key == "orders"),
    )
    assert item is not None
    assert item.asset_id is not None
    return item.asset_id


def _service(session: Session, store: MemoryObjectStore) -> AgentPackService:
    access: AccessContext = AccessContext(
        principal=Principal(
            subject="tester",
            email=None,
            name="Tester",
            roles=frozenset(),
            is_admin=True,
        ),
        user_id=uuid4(),
        workspace_id=None,
        member_workspace_ids=frozenset(),
        admin_workspace_ids=frozenset(),
        visible_space_ids=None,
    )
    return AgentPackService(
        session,
        store,
        AssetDefinitionsSettings(enabled=True, prefix="asset-definitions/"),
        access,
    )
