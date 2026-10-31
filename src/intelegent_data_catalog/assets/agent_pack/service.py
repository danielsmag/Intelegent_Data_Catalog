"""Read and write the markdown pack stored beside one asset declaration."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from intelegent_data_catalog.assets.agent_pack.compose import (
    LoadedPack,
    LoadedPrompt,
    LoadedRule,
    LoadedSkill,
)
from intelegent_data_catalog.assets.agent_pack.document import (
    MarkdownFile,
    check_slug,
    parse_markdown,
    render_markdown,
)
from intelegent_data_catalog.assets.agent_pack.models import (
    AgentCardRead,
    AgentDetail,
    AgentPackRead,
    AgentWrite,
    PromptDetail,
    PromptRead,
    PromptWrite,
    ReferenceRead,
    RuleDetail,
    RuleRead,
    RuleWrite,
    SkillDetail,
    SkillRead,
    SkillWrite,
)
from intelegent_data_catalog.assets.repository import AssetRepository
from intelegent_data_catalog.auth.access import AccessContext
from intelegent_data_catalog.core.settings import AssetDefinitionsSettings
from intelegent_data_catalog.exceptions import EntityNotFoundError, ValidationAppError
from intelegent_data_catalog.persistence.enums import AssetKind, DefinitionKind
from intelegent_data_catalog.persistence.models import (
    AssetDefinitionFileRow,
    AssetDefinitionItemRow,
    AssetRow,
)
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject

_MAX_FILES: int = 40
_TOOLS: dict[AssetKind, frozenset[str]] = {
    AssetKind.TABLE: frozenset({"metadata", "profile", "lineage", "graph"}),
    AssetKind.FILE: frozenset({"source", "outputs", "table_links"}),
    AssetKind.NOTEBOOK: frozenset({"source", "outputs", "table_links"}),
    AssetKind.AIRFLOW_DAG: frozenset({"source"}),
    AssetKind.AIRFLOW_INSTANCE: frozenset({"source"}),
}


class PackRoot:
    """The directory that contains one ``asset_*.py`` and its markdown."""

    def __init__(self, prefix: str, writable: bool, message: str | None) -> None:
        self.prefix: str = prefix
        self.writable: bool = writable
        self.message: str | None = message


class AgentPackService:
    """Lists and edits the definition-project files for one visible asset."""

    def __init__(
        self,
        session: Session,
        store: ObjectStore,
        settings: AssetDefinitionsSettings,
        access: AccessContext,
    ) -> None:
        self._session: Session = session
        self._store: ObjectStore = store
        self._settings: AssetDefinitionsSettings = settings
        self._access: AccessContext = access
        self._assets: AssetRepository = AssetRepository(session)

    def list_pack(self, asset_id: UUID) -> AgentPackRead:
        """Return prompts, rules, skills, and the agent card."""
        self._require_asset(asset_id)
        root: PackRoot | None = self._root(asset_id)
        if root is None or not root.writable:
            message: str | None = None if root is None else root.message
            return AgentPackRead(
                prefix=None if root is None else root.prefix,
                writable=False,
                message=message,
                agent=AgentCardRead(present=False),
                prompts=[],
                rules=[],
                skills=[],
            )
        objects: list[StoredObject] = self._store.list(root.prefix)
        return AgentPackRead(
            prefix=root.prefix,
            writable=True,
            message=None,
            agent=_agent_card(objects, root.prefix),
            prompts=_prompts(objects, root.prefix, self._store),
            rules=_rules(objects, root.prefix, self._store),
            skills=_skills(objects, root.prefix, self._store),
        )

    def get_prompt(self, asset_id: UUID, slug: str) -> PromptDetail:
        """Return one prompt, including its body."""
        root: PackRoot = self._writable(asset_id)
        key: str = f"{root.prefix}prompts/{check_slug(slug)}.md"
        parsed: MarkdownFile = _read(self._store, key)
        return PromptDetail(
            slug=slug,
            name=parsed.text("name", slug),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            active=parsed.flag("active", False),
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
        )

    def get_rule(self, asset_id: UUID, slug: str) -> RuleDetail:
        """Return one rule, including its body."""
        root: PackRoot = self._writable(asset_id)
        key: str = f"{root.prefix}rules/{check_slug(slug)}.md"
        parsed: MarkdownFile = _read(self._store, key)
        return RuleDetail(
            slug=slug,
            name=parsed.text("name", slug),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            order=parsed.number("order", 0),
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
        )

    def get_skill(self, asset_id: UUID, slug: str) -> SkillDetail:
        """Return one skill, including ``SKILL.md`` and reference files."""
        root: PackRoot = self._writable(asset_id)
        cleaned: str = check_slug(slug)
        key: str = f"{root.prefix}.agents/skills/{cleaned}/SKILL.md"
        parsed: MarkdownFile = _read(self._store, key)
        return SkillDetail(
            slug=cleaned,
            name=parsed.text("name", cleaned),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            tools=parsed.tools(),
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
            references=_references(
                self._store,
                f"{root.prefix}.agents/skills/{cleaned}/references/",
            ),
        )

    def get_agent(self, asset_id: UUID) -> AgentDetail:
        """Return ``AGENTS.md``."""
        root: PackRoot = self._writable(asset_id)
        key: str = f"{root.prefix}AGENTS.md"
        try:
            raw: bytes = self._store.get(key)
        except EntityNotFoundError:
            return AgentDetail(present=False, body="")
        return AgentDetail(
            present=True,
            updated_at=_modified(self._store.list(key), key),
            body=raw.decode("utf-8"),
        )

    def put_prompt(self, asset_id: UUID, slug: str, body: PromptWrite) -> PromptDetail:
        """Write one prompt. A second active prompt is rejected."""
        asset: AssetRow = self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        cleaned: str = _matching_slug(slug, body.name)
        if body.active:
            self._reject_other_active(root, cleaned)
        text: str = render_markdown(
            [
                ("name", cleaned),
                ("description", body.description),
                ("enabled", body.enabled),
                ("active", body.active),
            ],
            body.body,
        )
        key: str = f"{root.prefix}prompts/{cleaned}.md"
        self._put_new(root, key, text)
        del asset
        return self.get_prompt(asset_id, cleaned)

    def put_rule(self, asset_id: UUID, slug: str, body: RuleWrite) -> RuleDetail:
        """Write one rule."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        cleaned: str = _matching_slug(slug, body.name)
        text: str = render_markdown(
            [
                ("name", cleaned),
                ("description", body.description),
                ("enabled", body.enabled),
                ("order", body.order),
            ],
            body.body,
        )
        key: str = f"{root.prefix}rules/{cleaned}.md"
        self._put_new(root, key, text)
        return self.get_rule(asset_id, cleaned)

    def put_skill(self, asset_id: UUID, slug: str, body: SkillWrite) -> SkillDetail:
        """Write one ``SKILL.md``. The directory name and ``name`` must match."""
        asset: AssetRow = self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        cleaned: str = _matching_slug(slug, body.name)
        allowed: frozenset[str] = _TOOLS[AssetKind(asset.kind)]
        tool: str
        for tool in body.tools:
            if tool not in allowed:
                raise ValidationAppError(f"{tool} is not a tool for this asset", field="tools")
        text: str = render_markdown(
            [
                ("name", cleaned),
                ("description", body.description),
                ("enabled", body.enabled),
                ("metadata", {"tools": list(body.tools)}),
            ],
            body.body,
        )
        key: str = f"{root.prefix}.agents/skills/{cleaned}/SKILL.md"
        self._put_new(root, key, text)
        return self.get_skill(asset_id, cleaned)

    def put_agent(self, asset_id: UUID, body: AgentWrite) -> AgentDetail:
        """Write ``AGENTS.md`` without catalog frontmatter."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        text: str = body.body if body.body.endswith("\n") or body.body == "" else f"{body.body}\n"
        if len(text.encode()) > 32 * 1024:
            raise ValidationAppError("markdown file exceeds 32 KiB", field="body")
        self._store.put(f"{root.prefix}AGENTS.md", text.encode(), "text/markdown")
        return self.get_agent(asset_id)

    def delete(self, asset_id: UUID, kind: str, slug: str | None) -> None:
        """Remove one markdown file. The asset declaration stays."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        if kind == "agent":
            self._store.delete(f"{root.prefix}AGENTS.md")
            return
        cleaned: str = check_slug(slug or "")
        if kind == "prompts":
            self._store.delete(f"{root.prefix}prompts/{cleaned}.md")
            return
        if kind == "rules":
            self._store.delete(f"{root.prefix}rules/{cleaned}.md")
            return
        if kind == "skills":
            prefix: str = f"{root.prefix}.agents/skills/{cleaned}/"
            stored: StoredObject
            for stored in self._store.list(prefix):
                self._store.delete(stored.key)
            return
        raise ValidationAppError("unknown agent file kind", field="kind")

    def load(self, asset_id: UUID) -> LoadedPack:
        """Return the files a describe run should apply. Missing packs are empty."""
        loaded: LoadedPack = LoadedPack()
        root: PackRoot | None = self._root(asset_id)
        if root is None or not root.writable:
            return loaded
        loaded.prefix = root.prefix
        objects: list[StoredObject] = self._store.list(root.prefix)
        agent_key: str = f"{root.prefix}AGENTS.md"
        if _has(objects, agent_key):
            loaded.agents = self._store.get(agent_key).decode("utf-8")
        stored: StoredObject
        for stored in objects:
            relative: str = stored.key[len(root.prefix) :]
            prompt_file: bool = relative.startswith("prompts/") and relative.endswith(".md")
            if prompt_file and "/" not in relative[8:]:
                parsed: MarkdownFile = _read(self._store, stored.key)
                loaded.prompts.append(
                    LoadedPrompt(
                        parsed.text("name", relative[8:-3]),
                        parsed.body,
                        parsed.flag("enabled", True),
                        parsed.flag("active", False),
                    ),
                )
            elif (
                relative.startswith("rules/")
                and relative.endswith(".md")
                and relative.count("/") == 1
            ):
                parsed = _read(self._store, stored.key)
                loaded.rules.append(
                    LoadedRule(
                        parsed.text("name", relative[6:-3]),
                        parsed.body,
                        parsed.flag("enabled", True),
                        parsed.number("order", 0),
                    ),
                )
            elif relative.startswith(".agents/skills/") and relative.endswith("/SKILL.md"):
                parts: list[str] = relative.split("/")
                if len(parts) == 4:
                    parsed = _read(self._store, stored.key)
                    loaded.skills.append(
                        LoadedSkill(
                            parsed.text("name", parts[2]),
                            parsed.text("description"),
                            parsed.body,
                            parsed.flag("enabled", True),
                            parsed.tools(),
                        ),
                    )
        return loaded

    def _require_asset(self, asset_id: UUID) -> AssetRow:
        row: AssetRow | None = self._assets.get(asset_id)
        if row is None or not _can_see(row, self._access):
            raise EntityNotFoundError("asset", str(asset_id))
        return row

    def _writable(self, asset_id: UUID) -> PackRoot:
        self._require_asset(asset_id)
        root: PackRoot | None = self._root(asset_id)
        if root is None or not root.writable:
            message: str = "move the asset declaration into its own folder"
            if root is not None and root.message is not None:
                message = root.message
            raise ValidationAppError(message, field="prefix")
        return root

    def _root(self, asset_id: UUID) -> PackRoot | None:
        item: AssetDefinitionItemRow | None = self._session.scalar(
            select(AssetDefinitionItemRow).where(
                AssetDefinitionItemRow.asset_id == asset_id,
                AssetDefinitionItemRow.kind == DefinitionKind.ASSET.value,
            ),
        )
        if item is None:
            return None
        definition: AssetDefinitionFileRow | None = self._session.get(
            AssetDefinitionFileRow,
            item.file_id,
        )
        if definition is None:
            return None
        parent: str
        _file: str
        parent, _sep, _file = definition.key.rpartition("/")
        prefix: str = f"{parent}/"
        objects: list[StoredObject] = self._store.list(prefix)
        declarations: int = 0
        stored: StoredObject
        for stored in objects:
            relative: str = stored.key[len(prefix) :]
            if "/" in relative:
                continue
            name: str = relative.rsplit("/", 1)[-1]
            if name.startswith("asset_") and name.endswith(".py"):
                declarations += 1
        if declarations != 1:
            return PackRoot(
                prefix,
                False,
                "move this asset's asset_*.py into its own directory before adding agent files",
            )
        return PackRoot(prefix, True, None)

    def _reject_other_active(self, root: PackRoot, slug: str) -> None:
        stored: StoredObject
        for stored in self._store.list(f"{root.prefix}prompts/"):
            relative: str = stored.key[len(root.prefix) :]
            if not relative.endswith(".md") or relative.count("/") != 1:
                continue
            other: str = relative[len("prompts/") : -3]
            if other == slug:
                continue
            parsed: MarkdownFile = _read(self._store, stored.key)
            if parsed.flag("active", False):
                raise ValidationAppError(
                    f"prompt {other} is already active",
                    field="active",
                )

    def _put_new(self, root: PackRoot, key: str, text: str) -> None:
        existing: set[str] = {item.key for item in self._store.list(root.prefix)}
        if key not in existing and len(existing) >= _MAX_FILES:
            raise ValidationAppError("this asset already has 40 agent files", field="slug")
        self._store.put(key, text.encode(), "text/markdown")


def _can_see(row: AssetRow, access: AccessContext) -> bool:
    personal: bool = row.owner_user_id is not None and row.owner_user_id != access.user_id
    if personal and not access.principal.is_admin:
        return False
    return access.is_member(row.workspace_id)


def _read(store: ObjectStore, key: str) -> MarkdownFile:
    try:
        raw: bytes = store.get(key)
    except EntityNotFoundError:
        raise EntityNotFoundError("agent file", key) from None
    return parse_markdown(raw.decode("utf-8"))


def _matching_slug(slug: str, name: str) -> str:
    cleaned: str = check_slug(slug)
    if name.strip() != cleaned:
        raise ValidationAppError("name must match the file slug", field="name")
    return cleaned


def _has(objects: list[StoredObject], key: str) -> bool:
    return any(stored.key == key for stored in objects)


def _modified(objects: list[StoredObject], key: str) -> datetime | None:
    stored: StoredObject
    for stored in objects:
        if stored.key == key:
            return stored.last_modified
    return None


def _agent_card(objects: list[StoredObject], prefix: str) -> AgentCardRead:
    key: str = f"{prefix}AGENTS.md"
    return AgentCardRead(present=_has(objects, key), updated_at=_modified(objects, key))


def _prompts(objects: list[StoredObject], prefix: str, store: ObjectStore) -> list[PromptRead]:
    found: list[PromptRead] = []
    stored: StoredObject
    for stored in objects:
        relative: str = stored.key[len(prefix) :]
        if not relative.startswith("prompts/") or not relative.endswith(".md"):
            continue
        if relative.count("/") != 1:
            continue
        slug: str = relative[len("prompts/") : -3]
        try:
            parsed: MarkdownFile = parse_markdown(store.get(stored.key).decode("utf-8"))
        except (EntityNotFoundError, ValidationAppError, UnicodeError):
            continue
        found.append(
            PromptRead(
                slug=slug,
                name=parsed.text("name", slug),
                description=parsed.text("description"),
                enabled=parsed.flag("enabled", True),
                active=parsed.flag("active", False),
                updated_at=stored.last_modified,
            ),
        )
    found.sort(key=lambda item: item.slug)
    return found


def _rules(objects: list[StoredObject], prefix: str, store: ObjectStore) -> list[RuleRead]:
    found: list[RuleRead] = []
    stored: StoredObject
    for stored in objects:
        relative: str = stored.key[len(prefix) :]
        rule_file: bool = relative.startswith("rules/") and relative.endswith(".md")
        if not rule_file or relative.count("/") != 1:
            continue
        slug: str = relative[len("rules/") : -3]
        try:
            parsed: MarkdownFile = parse_markdown(store.get(stored.key).decode("utf-8"))
        except (EntityNotFoundError, ValidationAppError, UnicodeError):
            continue
        found.append(
            RuleRead(
                slug=slug,
                name=parsed.text("name", slug),
                description=parsed.text("description"),
                enabled=parsed.flag("enabled", True),
                order=parsed.number("order", 0),
                updated_at=stored.last_modified,
            ),
        )
    found.sort(key=lambda item: (item.order, item.slug))
    return found


def _skills(objects: list[StoredObject], prefix: str, store: ObjectStore) -> list[SkillRead]:
    found: list[SkillRead] = []
    stored: StoredObject
    for stored in objects:
        relative: str = stored.key[len(prefix) :]
        if not relative.endswith("/SKILL.md") or not relative.startswith(".agents/skills/"):
            continue
        parts: list[str] = relative.split("/")
        if len(parts) != 4:
            continue
        slug: str = parts[2]
        try:
            parsed: MarkdownFile = parse_markdown(store.get(stored.key).decode("utf-8"))
        except (EntityNotFoundError, ValidationAppError, UnicodeError):
            continue
        found.append(
            SkillRead(
                slug=slug,
                name=parsed.text("name", slug),
                description=parsed.text("description"),
                enabled=parsed.flag("enabled", True),
                tools=parsed.tools(),
                updated_at=stored.last_modified,
            ),
        )
    found.sort(key=lambda item: item.slug)
    return found


def _references(store: ObjectStore, prefix: str) -> list[ReferenceRead]:
    found: list[ReferenceRead] = []
    stored: StoredObject
    for stored in store.list(prefix):
        relative: str = stored.key[len(prefix) :]
        if relative == "" or "/" in relative or not relative.endswith(".md"):
            continue
        try:
            body: str = store.get(stored.key).decode("utf-8")
        except (EntityNotFoundError, UnicodeError):
            continue
        found.append(ReferenceRead(name=relative[: -len(".md")], body=body))
    found.sort(key=lambda item: item.name)
    return found

