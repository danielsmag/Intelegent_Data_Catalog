"""Stage an agent directory and render its enabled skills into the prompt."""

import tempfile
from pathlib import Path

from intelegent_data_catalog.agents.agent_pack.compose import LoadedPack, LoadedSkill
from intelegent_data_catalog.agents.agent_pack.document import harness_skill, parse_markdown
from intelegent_data_catalog.storage.object_store import ObjectStore, StoredObject


def render_skills(store: ObjectStore, pack: LoadedPack) -> str:
    """Render enabled skills. Describe stays one structured model call."""
    names: list[str] = pack.enabled_skill_names()
    if len(pack.layers) == 0 or len(names) == 0:
        return ""
    with tempfile.TemporaryDirectory() as directory:
        root: Path = Path(directory)
        _stage_layers(store, pack, root)
        parts: list[str] = []
        name: str
        for name in names:
            path: Path = root.joinpath(".agents", "skills", name, "SKILL.md")
            if not path.is_file():
                continue
            parsed = parse_markdown(path.read_text(encoding="utf-8"))
            title: str = parsed.text("name", name)
            description: str = parsed.text("description")
            body: str = parsed.body.strip()
            block: str = f"# {title}\n\n{description}\n\n{body}".strip()
            parts.append(block)
    return "\n\n".join(parts)


def _stage_layers(store: ObjectStore, pack: LoadedPack, root: Path) -> None:
    """Copy each layer onto ``root``. Later layers overwrite earlier files."""
    base: str = pack.layers[0]
    skipped: list[str] = []
    layer: str
    for layer in pack.layers[1:]:
        if layer.startswith(base):
            skipped.append(layer[len(base) :])
    _stage(store, base, root, pack.skills, tuple(skipped))
    for layer in pack.layers[1:]:
        _stage(store, layer, root, pack.skills, ())


def _stage(
    store: ObjectStore,
    prefix: str,
    root: Path,
    skills: list[LoadedSkill],
    skip: tuple[str, ...] = (),
) -> None:
    by_name: dict[str, LoadedSkill] = {skill.name: skill for skill in skills}
    stored: StoredObject
    for stored in store.list(prefix):
        relative: str = stored.key[len(prefix) :]
        if relative == "" or relative.endswith("/") or ".." in relative.split("/"):
            continue
        if any(relative == item or relative.startswith(item) for item in skip):
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
