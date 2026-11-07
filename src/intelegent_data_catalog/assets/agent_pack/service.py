"""Read and write the markdown pack stored beside one asset declaration."""

import asyncio
import re
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from pydantic_ai_harness.memory import MemoryFile
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
from intelegent_data_catalog.assets.agent_pack.memory_store import ObjectMemoryStore
from intelegent_data_catalog.assets.agent_pack.models import (
    AgentCardRead,
    AgentDetail,
    AgentPackRead,
    AgentWrite,
    MemoryFileDetail,
    MemoryFileRead,
    PackLayerRead,
    PackLimitsRead,
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
from intelegent_data_catalog.assets.definitions.layout import NAME_PREFIX
from intelegent_data_catalog.assets.definitions.models import AssetSpec
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.registry import operator_from_ref
from intelegent_data_catalog.assets.dsl.operators.tables.schema import SchemaOperator
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

_MEMORY_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_MEMORY_CHARS: int = 24_000
_TOOLS: dict[AssetKind, frozenset[str]] = {
    AssetKind.TABLE: frozenset({"metadata", "profile", "lineage", "graph"}),
    AssetKind.FILE: frozenset({"source", "outputs", "table_links"}),
    AssetKind.NOTEBOOK: frozenset({"source", "outputs", "table_links"}),
    AssetKind.AIRFLOW_DAG: frozenset({"source"}),
    AssetKind.AIRFLOW_INSTANCE: frozenset({"source"}),
}


class PackLayer:
    """One directory of agent files. ``name`` is ``asset``, ``schema``, or ``table``."""

    def __init__(self, name: str, prefix: str) -> None:
        self.name: str = name
        self.prefix: str = prefix


class PackLimits:
    """Per-layer file cap and whether only one prompt may be active."""

    def __init__(self, max_files: int, single_active_prompt: bool) -> None:
        self.max_files: int = max_files
        self.single_active_prompt: bool = single_active_prompt


_DEFAULT_LIMITS: PackLimits = PackLimits(40, True)


class _LayerPack:
    """Files read from one layer, keyed by file slug."""

    def __init__(self) -> None:
        self.agents: str = ""
        self.agents_present: bool = False
        self.prompts: dict[str, LoadedPrompt] = {}
        self.rules: dict[str, LoadedRule] = {}
        self.skills: dict[str, LoadedSkill] = {}


class PackRoot:
    """Directories that contribute agent files for one asset. Base layer first."""

    def __init__(
        self,
        layers: list[PackLayer],
        limits: PackLimits,
        writable: bool,
        message: str | None,
    ) -> None:
        self.layers: list[PackLayer] = layers
        self.limits: PackLimits = limits
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
            return _empty_pack(root)
        return self._merged_pack(root)

    def _merged_pack(self, root: PackRoot) -> AgentPackRead:
        agent: AgentCardRead = AgentCardRead(present=False, layer=root.layers[0].name)
        prompts: dict[str, PromptRead] = {}
        rules: dict[str, RuleRead] = {}
        skills: dict[str, SkillRead] = {}
        layer: PackLayer
        for layer in root.layers:
            objects: list[StoredObject] = self._layer_objects(layer.prefix)
            card: AgentCardRead = _agent_card(objects, layer.prefix).model_copy(
                update={"layer": layer.name},
            )
            if card.present:
                agent = card
            prompt: PromptRead
            for prompt in _prompts(objects, layer.prefix, self._store):
                prompts[prompt.slug] = prompt.model_copy(update={"layer": layer.name})
            rule: RuleRead
            for rule in _rules(objects, layer.prefix, self._store):
                rules[rule.slug] = rule.model_copy(update={"layer": layer.name})
            skill: SkillRead
            for skill in _skills(objects, layer.prefix, self._store):
                skills[skill.slug] = skill.model_copy(update={"layer": layer.name})
        base_name: str = root.layers[0].name
        if any(item.active and item.layer != base_name for item in prompts.values()):
            slug: str
            for slug, prompt in list(prompts.items()):
                if prompt.layer == base_name and prompt.active:
                    prompts[slug] = prompt.model_copy(update={"active": False})
        return AgentPackRead(
            prefix=root.layers[0].prefix,
            writable=True,
            message=None,
            layers=_layer_reads(root),
            limits=_limits_read(root.limits),
            agent=agent,
            prompts=sorted(prompts.values(), key=lambda item: item.slug),
            rules=sorted(rules.values(), key=lambda item: (item.order, item.slug)),
            skills=sorted(skills.values(), key=lambda item: item.slug),
        )

    def get_prompt(self, asset_id: UUID, slug: str) -> PromptDetail:
        """Return one prompt, including its body. A table file wins over the schema file."""
        root: PackRoot = self._writable(asset_id)
        cleaned: str = check_slug(slug)
        layer, key = self._winning(root, f"prompts/{cleaned}.md")
        parsed: MarkdownFile = _read(self._store, key)
        active: bool = parsed.flag("active", False)
        if active and layer is not root.layers[-1] and self._later_prompt_active(root, layer):
            active = False
        return PromptDetail(
            slug=cleaned,
            name=parsed.text("name", cleaned),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            active=active,
            layer=layer.name,
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
        )

    def get_rule(self, asset_id: UUID, slug: str) -> RuleDetail:
        """Return one rule, including its body. A table file wins over the schema file."""
        root: PackRoot = self._writable(asset_id)
        cleaned: str = check_slug(slug)
        layer, key = self._winning(root, f"rules/{cleaned}.md")
        parsed: MarkdownFile = _read(self._store, key)
        return RuleDetail(
            slug=cleaned,
            name=parsed.text("name", cleaned),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            order=parsed.number("order", 0),
            layer=layer.name,
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
        )

    def get_skill(self, asset_id: UUID, slug: str) -> SkillDetail:
        """Return one skill. Reference files merge by name, later layer wins."""
        root: PackRoot = self._writable(asset_id)
        cleaned: str = check_slug(slug)
        relative: str = f".agents/skills/{cleaned}/SKILL.md"
        layer, key = self._winning(root, relative)
        parsed: MarkdownFile = _read(self._store, key)
        return SkillDetail(
            slug=cleaned,
            name=parsed.text("name", cleaned),
            description=parsed.text("description"),
            enabled=parsed.flag("enabled", True),
            tools=parsed.tools(),
            layer=layer.name,
            updated_at=_modified(self._store.list(key), key),
            body=parsed.body,
            references=self._merged_references(root, cleaned),
        )

    def get_agent(self, asset_id: UUID) -> AgentDetail:
        """Return ``AGENTS.md``. A table card replaces the schema card."""
        root: PackRoot = self._writable(asset_id)
        try:
            layer, key = self._winning(root, "AGENTS.md")
        except EntityNotFoundError:
            return AgentDetail(present=False, body="", layer=root.layers[0].name)
        return AgentDetail(
            present=True,
            layer=layer.name,
            updated_at=_modified(self._store.list(key), key),
            body=self._store.get(key).decode("utf-8"),
        )

    def put_prompt(
        self,
        asset_id: UUID,
        slug: str,
        body: PromptWrite,
        layer: str | None = None,
    ) -> PromptDetail:
        """Write one prompt. A second active prompt in the same layer is rejected."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        target: PackLayer = self._target(root, layer)
        cleaned: str = _matching_slug(slug, body.name)
        if body.active and root.limits.single_active_prompt:
            self._reject_other_active(target, cleaned)
        text: str = render_markdown(
            [
                ("name", cleaned),
                ("description", body.description),
                ("enabled", body.enabled),
                ("active", body.active),
            ],
            body.body,
        )
        self._put_new(root, target, f"{target.prefix}prompts/{cleaned}.md", text)
        return self.get_prompt(asset_id, cleaned)

    def put_rule(
        self,
        asset_id: UUID,
        slug: str,
        body: RuleWrite,
        layer: str | None = None,
    ) -> RuleDetail:
        """Write one rule into ``layer``. Omitted ``layer`` is the most specific one."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        target: PackLayer = self._target(root, layer)
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
        self._put_new(root, target, f"{target.prefix}rules/{cleaned}.md", text)
        return self.get_rule(asset_id, cleaned)

    def put_skill(
        self,
        asset_id: UUID,
        slug: str,
        body: SkillWrite,
        layer: str | None = None,
    ) -> SkillDetail:
        """Write one ``SKILL.md``. The directory name and ``name`` must match."""
        asset: AssetRow = self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        target: PackLayer = self._target(root, layer)
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
        key: str = f"{target.prefix}.agents/skills/{cleaned}/SKILL.md"
        self._put_new(root, target, key, text)
        return self.get_skill(asset_id, cleaned)

    def put_agent(self, asset_id: UUID, body: AgentWrite, layer: str | None = None) -> AgentDetail:
        """Write ``AGENTS.md`` without catalog frontmatter."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        target: PackLayer = self._target(root, layer)
        text: str = body.body if body.body.endswith("\n") or body.body == "" else f"{body.body}\n"
        if len(text.encode()) > 32 * 1024:
            raise ValidationAppError("markdown file exceeds 32 KiB", field="body")
        self._put_new(root, target, f"{target.prefix}AGENTS.md", text)
        return self.get_agent(asset_id)

    def delete(self, asset_id: UUID, kind: str, slug: str | None, layer: str | None = None) -> None:
        """Remove one markdown file from ``layer``. The asset declaration stays."""
        self._require_asset(asset_id)
        root: PackRoot = self._writable(asset_id)
        target: PackLayer = self._target(root, layer)
        if kind == "agent":
            self._store.delete(f"{target.prefix}AGENTS.md")
            return
        cleaned: str = check_slug(slug or "")
        if kind == "prompts":
            self._store.delete(f"{target.prefix}prompts/{cleaned}.md")
            return
        if kind == "rules":
            self._store.delete(f"{target.prefix}rules/{cleaned}.md")
            return
        if kind == "skills":
            prefix: str = f"{target.prefix}.agents/skills/{cleaned}/"
            stored: StoredObject
            for stored in self._store.list(prefix):
                self._store.delete(stored.key)
            return
        raise ValidationAppError("unknown agent file kind", field="kind")

    def list_memory(self, asset_id: UUID) -> list[MemoryFileRead]:
        """Return the notes the model stored for this asset."""
        self._require_asset(asset_id)
        prefix: str = f"agent-memory/{asset_id}/main/"
        found: list[MemoryFileRead] = []
        stored: StoredObject
        for stored in self._store.list(prefix):
            name: str = stored.key[len(prefix) :]
            if not _visible_memory_name(name):
                continue
            found.append(MemoryFileRead(name=name, updated_at=stored.last_modified))
        found.sort(key=_memory_order)
        return found

    def get_memory(self, asset_id: UUID, name: str) -> MemoryFileDetail:
        """Return one notebook file for the preview."""
        self._require_asset(asset_id)
        cleaned: str = _memory_name(name)
        path: str = f"{asset_id}/main/{cleaned}"
        key: str = f"agent-memory/{path}"
        store: ObjectMemoryStore = ObjectMemoryStore(self._store, "agent-memory/")
        found: MemoryFile | None = asyncio.run(store.read(path, max_chars=_MEMORY_CHARS))
        if found is None:
            raise EntityNotFoundError("agent memory", cleaned)
        updated_by: str | None = None
        if found.metadata is not None:
            updated_by = found.metadata.get("updated_by")
        return MemoryFileDetail(
            name=cleaned,
            body=found.content,
            updated_by=updated_by,
            updated_at=_modified(self._store.list(key), key),
        )

    def load(self, asset_id: UUID) -> LoadedPack:
        """Return the files a describe run should apply. Missing packs are empty."""
        loaded: LoadedPack = LoadedPack()
        root: PackRoot | None = self._root(asset_id)
        if root is None or not root.writable:
            return loaded
        loaded.layers = [layer.prefix for layer in root.layers]
        prompts: dict[str, LoadedPrompt] = {}
        rules: dict[str, LoadedRule] = {}
        skills: dict[str, LoadedSkill] = {}
        agents: str = ""
        index: int
        layer: PackLayer
        for index, layer in enumerate(root.layers):
            part: _LayerPack = self._read_layer(layer.prefix)
            if part.agents_present:
                agents = part.agents
            overlay_active: bool = index > 0 and any(
                prompt.active for prompt in part.prompts.values()
            )
            if overlay_active:
                current: LoadedPrompt
                for current in prompts.values():
                    current.active = False
            prompts.update(part.prompts)
            rules.update(part.rules)
            skills.update(part.skills)
        loaded.agents = agents
        loaded.prompts = list(prompts.values())
        loaded.rules = list(rules.values())
        loaded.skills = list(skills.values())
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
        item: AssetDefinitionItemRow | None = self._session.scalars(
            select(AssetDefinitionItemRow)
            .where(
                AssetDefinitionItemRow.asset_id == asset_id,
                AssetDefinitionItemRow.kind == DefinitionKind.ASSET.value,
            )
            .order_by(AssetDefinitionItemRow.item_key),
        ).first()
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
        limits: PackLimits = _limits(item)
        layers: list[PackLayer] = [PackLayer("asset", prefix)]
        segment: str | None = _overlay_segment(item)
        if segment is not None:
            marker: str = NAME_PREFIX[DefinitionKind.ASSET]
            overlay: str = f"{prefix}{marker}{segment}/"
            layers = [PackLayer("schema", prefix), PackLayer("table", overlay)]
            if _declaration_count(self._store, overlay) > 0:
                return PackRoot(
                    layers,
                    limits,
                    False,
                    f"overlay folder {marker}{segment} holds another asset declaration",
                )
        if _declaration_count(self._store, prefix) != 1:
            return PackRoot(
                layers,
                limits,
                False,
                "move this asset's asset_*.py into its own directory before adding agent files",
            )
        return PackRoot(layers, limits, True, None)

    def _target(self, root: PackRoot, layer: str | None) -> PackLayer:
        if layer is None:
            return root.layers[-1]
        found: PackLayer
        for found in root.layers:
            if found.name == layer:
                return found
        raise ValidationAppError("unknown agent layer", field="layer")

    def _winning(self, root: PackRoot, relative: str) -> tuple[PackLayer, str]:
        found: tuple[PackLayer, str] | None = None
        layer: PackLayer
        for layer in root.layers:
            key: str = f"{layer.prefix}{relative}"
            if _has(self._store.list(key), key):
                found = (layer, key)
        if found is None:
            raise EntityNotFoundError("agent file", relative)
        return found

    def _later_prompt_active(self, root: PackRoot, layer: PackLayer) -> bool:
        """True when a more specific layer has an active prompt."""
        started: bool = False
        candidate: PackLayer
        for candidate in root.layers:
            if candidate is layer:
                started = True
                continue
            if not started:
                continue
            part: _LayerPack = self._read_layer(candidate.prefix)
            if any(prompt.active for prompt in part.prompts.values()):
                return True
        return False

    def _merged_references(self, root: PackRoot, slug: str) -> list[ReferenceRead]:
        found: dict[str, ReferenceRead] = {}
        layer: PackLayer
        for layer in root.layers:
            item: ReferenceRead
            for item in _references(
                self._store,
                f"{layer.prefix}.agents/skills/{slug}/references/",
            ):
                found[item.name] = item
        names: list[str] = sorted(found)
        return [found[name] for name in names]

    def _read_layer(self, prefix: str) -> _LayerPack:
        part: _LayerPack = _LayerPack()
        objects: list[StoredObject] = self._layer_objects(prefix)
        agent_key: str = f"{prefix}AGENTS.md"
        if _has(objects, agent_key):
            part.agents_present = True
            part.agents = self._store.get(agent_key).decode("utf-8")
        stored: StoredObject
        for stored in objects:
            relative: str = stored.key[len(prefix) :]
            prompt_file: bool = relative.startswith("prompts/") and relative.endswith(".md")
            if prompt_file and "/" not in relative[8:]:
                parsed: MarkdownFile = _read(self._store, stored.key)
                slug: str = relative[8:-3]
                part.prompts[slug] = LoadedPrompt(
                    parsed.text("name", slug),
                    parsed.body,
                    parsed.flag("enabled", True),
                    parsed.flag("active", False),
                )
            elif (
                relative.startswith("rules/")
                and relative.endswith(".md")
                and relative.count("/") == 1
            ):
                parsed = _read(self._store, stored.key)
                slug = relative[6:-3]
                part.rules[slug] = LoadedRule(
                    parsed.text("name", slug),
                    parsed.body,
                    parsed.flag("enabled", True),
                    parsed.number("order", 0),
                )
            elif relative.startswith(".agents/skills/") and relative.endswith("/SKILL.md"):
                parts: list[str] = relative.split("/")
                if len(parts) == 4:
                    parsed = _read(self._store, stored.key)
                    part.skills[parts[2]] = LoadedSkill(
                        parsed.text("name", parts[2]),
                        parsed.text("description"),
                        parsed.body,
                        parsed.flag("enabled", True),
                        parsed.tools(),
                    )
        return part

    def _layer_objects(self, prefix: str) -> list[StoredObject]:
        """Objects in one layer. Nested overlay folders are left out."""
        nested: list[str] = []
        marker: str = NAME_PREFIX[DefinitionKind.ASSET]
        found: list[StoredObject] = []
        stored: StoredObject
        for stored in self._store.list(prefix):
            relative: str = stored.key[len(prefix) :]
            if relative.startswith(marker) and "/" in relative:
                folder: str = relative.split("/", 1)[0]
                nested_prefix: str = f"{prefix}{folder}/"
                if nested_prefix not in nested:
                    nested.append(nested_prefix)
            if any(stored.key.startswith(item) for item in nested):
                continue
            found.append(stored)
        return found

    def _reject_other_active(self, layer: PackLayer, slug: str) -> None:
        stored: StoredObject
        for stored in self._store.list(f"{layer.prefix}prompts/"):
            relative: str = stored.key[len(layer.prefix) :]
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

    def _put_new(self, root: PackRoot, layer: PackLayer, key: str, text: str) -> None:
        existing: set[str] = {item.key for item in self._layer_objects(layer.prefix)}
        if key not in existing and len(existing) >= root.limits.max_files:
            raise ValidationAppError(
                f"this layer already has {root.limits.max_files} agent files",
                field="slug",
            )
        self._store.put(key, text.encode(), "text/markdown")


def _overlay_segment(item: AssetDefinitionItemRow) -> str | None:
    """Table name for a schema child. ``None`` for a normal asset."""
    try:
        spec: AssetSpec = AssetSpec.model_validate(item.declaration)
    except ValidationError:
        return None
    if spec.parent_key is None or "/" not in item.item_key:
        return None
    segment: str = item.item_key.split("/", 1)[1]
    if segment == "" or "/" in segment or ".." in segment:
        return None
    return segment


def _limits(item: AssetDefinitionItemRow) -> PackLimits:
    """Pack caps from a schema operator. Anything else keeps the defaults."""
    try:
        spec: AssetSpec = AssetSpec.model_validate(item.declaration)
    except ValidationError:
        return _DEFAULT_LIMITS
    if spec.operator is None:
        return _DEFAULT_LIMITS
    try:
        operator: object = operator_from_ref(spec.operator)
    except AssetDefinitionError:
        return _DEFAULT_LIMITS
    if isinstance(operator, SchemaOperator):
        return PackLimits(operator.pack_max_files, operator.pack_single_active_prompt)
    return _DEFAULT_LIMITS


def _declaration_count(store: ObjectStore, prefix: str) -> int:
    """Count ``asset_*.py`` files directly inside ``prefix``."""
    marker: str = NAME_PREFIX[DefinitionKind.ASSET]
    count: int = 0
    stored: StoredObject
    for stored in store.list(prefix):
        relative: str = stored.key[len(prefix) :]
        if "/" in relative:
            continue
        name: str = relative.rsplit("/", 1)[-1]
        if name.startswith(marker) and name.endswith(".py"):
            count += 1
    return count


def _empty_pack(root: PackRoot | None) -> AgentPackRead:
    prefix: str | None = None
    message: str | None = None
    layers: list[PackLayerRead] = []
    limits: PackLimits = _DEFAULT_LIMITS
    if root is not None:
        message = root.message
        limits = root.limits
        layers = _layer_reads(root)
        if len(root.layers) > 0:
            prefix = root.layers[0].prefix
    return AgentPackRead(
        prefix=prefix,
        writable=False,
        message=message,
        layers=layers,
        limits=_limits_read(limits),
        agent=AgentCardRead(present=False),
        prompts=[],
        rules=[],
        skills=[],
    )


def _layer_reads(root: PackRoot) -> list[PackLayerRead]:
    return [PackLayerRead(name=layer.name, prefix=layer.prefix) for layer in root.layers]


def _limits_read(limits: PackLimits) -> PackLimitsRead:
    return PackLimitsRead(
        max_files=limits.max_files,
        single_active_prompt=limits.single_active_prompt,
    )


def _memory_order(item: MemoryFileRead) -> tuple[bool, str]:
    return (item.name != "MEMORY.md", item.name)


def _visible_memory_name(name: str) -> bool:
    if "/" in name or not name.endswith(".md") or ".." in name:
        return False
    return _MEMORY_NAME.fullmatch(name) is not None


def _memory_name(name: str) -> str:
    if not _visible_memory_name(name):
        raise ValidationAppError("memory file name is invalid", field="name")
    return name


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

