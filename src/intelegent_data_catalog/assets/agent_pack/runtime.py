"""Stage an asset directory and ask the harness to render its skills."""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai.capabilities import Capability, LocalWorkspace
from pydantic_ai.workspaces import LocalWorkspaceBackend
from pydantic_ai_harness import RepoContext, Skills

from intelegent_data_catalog.assets.agent_pack.compose import LoadedPack, LoadedSkill
from intelegent_data_catalog.assets.agent_pack.document import harness_skill
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject


def render_skills(store: ObjectStore, pack: LoadedPack) -> str:
    """Host-invoke enabled skills. Describe stays one structured model call."""
    names: list[str] = pack.enabled_skill_names()
    if pack.prefix is None or len(names) == 0:
        return ""
    with tempfile.TemporaryDirectory() as directory:
        root: Path = Path(directory)
        _stage(store, pack.prefix, root, pack.skills)
        skills: Skills[object] = Skills(
            ".agents/skills",
            include=names,
            missing_directories="skip",
        )
        catalog = asyncio.run(skills.load(LocalWorkspaceBackend(root)))
        parts: list[str] = [item.render() for item in catalog.skills]
    return "\n\n".join(parts)


def operation_capabilities(staged_dir: Path, skill_names: list[str]) -> list[object]:
    """Capabilities for a tool-using run. Describe does not use this list."""
    capabilities: list[object] = [
        LocalWorkspace(staged_dir),
        RepoContext(
            filenames=("AGENTS.md",),
            expose_inventory_tool=False,
            nested_traversal=False,
        ),
    ]
    if len(skill_names) > 0:
        capabilities.append(
            Skills(
                ".agents/skills",
                include=skill_names,
                missing_directories="skip",
            ),
        )
    return capabilities


def rules_capability(text: str | None) -> Capability[object] | None:
    """Always-on rules. The harness has no rules loader."""
    if text is None or text.strip() == "":
        return None
    return Capability(id="asset-rules", instructions=text)


def _stage(store: ObjectStore, prefix: str, root: Path, skills: list[LoadedSkill]) -> None:
    by_name: dict[str, LoadedSkill] = {skill.name: skill for skill in skills}
    stored: StoredObject
    for stored in store.list(prefix):
        relative: str = stored.key[len(prefix) :]
        if relative == "" or relative.endswith("/") or ".." in relative.split("/"):
            continue
        target: Path = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith("/SKILL.md"):
            parts: list[str] = relative.split("/")
            skill: LoadedSkill | None = by_name.get(parts[2]) if len(parts) == 4 else None
            if skill is None:
                continue
            target.write_text(
                harness_skill(skill.name, skill.description, skill.body),
                encoding="utf-8",
            )
            continue
        target.write_bytes(store.get(stored.key))
