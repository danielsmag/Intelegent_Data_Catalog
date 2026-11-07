"""Turn a loaded pack into the text a describe run adds."""

from intelegent_data_catalog.persistence.enums import AgentKind

_TABLE_FLAG: dict[str, str] = {
    "profile": "include_data_profile",
    "lineage": "include_lineage",
    "graph": "include_graph",
}
_OTHER_FLAG: dict[str, str] = {
    "source": "include_source",
    "outputs": "include_outputs",
    "table_links": "include_table_links",
}


class LoadedRule:
    """One rule after the file is parsed."""

    def __init__(self, name: str, body: str, enabled: bool, order: int) -> None:
        self.name: str = name
        self.body: str = body
        self.enabled: bool = enabled
        self.order: int = order


class LoadedSkill:
    """One skill after ``SKILL.md`` is parsed."""

    def __init__(
        self,
        name: str,
        description: str,
        body: str,
        enabled: bool,
        tools: list[str],
    ) -> None:
        self.name: str = name
        self.description: str = description
        self.body: str = body
        self.enabled: bool = enabled
        self.tools: list[str] = tools


class LoadedPrompt:
    """One prompt after the file is parsed."""

    def __init__(self, name: str, body: str, enabled: bool, active: bool) -> None:
        self.name: str = name
        self.body: str = body
        self.enabled: bool = enabled
        self.active: bool = active


class LoadedPack:
    """Files that apply to one agent run."""

    def __init__(self) -> None:
        self.agents: str = ""
        self.rules: list[LoadedRule] = []
        self.skills: list[LoadedSkill] = []
        self.prompts: list[LoadedPrompt] = []
        self.layers: list[str] = []

    def enabled_skill_names(self) -> list[str]:
        """Skill directory names the harness should expose."""
        names: list[str] = []
        skill: LoadedSkill
        for skill in self.skills:
            if skill.enabled and skill.description.strip() != "":
                names.append(skill.name)
        return names


def scoped_pack(pack: LoadedPack, flags: dict[str, bool]) -> LoadedPack:
    """Drop rules or skills when that agent switch is off. A missing switch stays on."""
    use_rules: bool = flags.get("use_rules", True)
    use_skills: bool = flags.get("use_skills", True)
    if use_rules and use_skills:
        return pack
    scoped: LoadedPack = LoadedPack()
    scoped.agents = pack.agents
    scoped.prompts = list(pack.prompts)
    scoped.layers = list(pack.layers)
    scoped.rules = list(pack.rules) if use_rules else []
    scoped.skills = list(pack.skills) if use_skills else []
    return scoped


def compose_rules(rules: list[LoadedRule]) -> str | None:
    """Join enabled rules by order, then name."""
    chosen: list[LoadedRule] = [rule for rule in rules if rule.enabled and rule.body.strip() != ""]
    chosen.sort(key=_rule_sort)
    if len(chosen) == 0:
        return None
    parts: list[str] = []
    rule: LoadedRule
    for rule in chosen:
        parts.append(f"Rule {rule.name}:\n{rule.body.strip()}")
    return "\n\n".join(parts)


def active_task(prompts: list[LoadedPrompt]) -> str | None:
    """Return enabled active prompt bodies, joined in name order."""
    chosen: list[LoadedPrompt] = [
        prompt
        for prompt in prompts
        if prompt.enabled and prompt.active and prompt.body.strip() != ""
    ]
    chosen.sort(key=lambda prompt: prompt.name)
    if len(chosen) == 0:
        return None
    return "\n\n".join(prompt.body.strip() for prompt in chosen)


def allowed_tools(
    skills: list[LoadedSkill],
    flags: dict[str, bool],
    kind: AgentKind,
) -> set[str] | None:
    """Return tool ids this run may use.

    ``None`` means no enabled skill named a tool, so today's flags and tools stay.
    """
    requested: set[str] = set()
    skill: LoadedSkill
    for skill in skills:
        if skill.enabled:
            requested.update(skill.tools)
    if len(requested) == 0:
        return None
    allowed: set[str] = set()
    tool_id: str
    for tool_id in requested:
        if tool_id == "metadata" and kind == AgentKind.TABLE:
            allowed.add(tool_id)
            continue
        flag: str | None = _flag_for(kind, tool_id)
        if flag is not None and flags.get(flag, False):
            allowed.add(tool_id)
    return allowed


def narrow_flags(flags: dict[str, bool], allowed: set[str] | None) -> dict[str, bool]:
    """Drop evidence the enabled skills did not request."""
    if allowed is None:
        return flags
    narrowed: dict[str, bool] = dict(flags)
    tool_id: str
    flag: str
    for tool_id, flag in {**_TABLE_FLAG, **_OTHER_FLAG}.items():
        if tool_id not in allowed and flag in narrowed:
            narrowed[flag] = False
    return narrowed


def describe_instructions(agents: str, rules: str | None, skills: str) -> str | None:
    """Join the agent card, rules, and host-rendered skills."""
    parts: list[str] = []
    if agents.strip() != "":
        parts.append(agents.strip())
    if rules is not None and rules.strip() != "":
        parts.append(rules.strip())
    if skills.strip() != "":
        parts.append(skills.strip())
    if len(parts) == 0:
        return None
    return "\n\n".join(parts)


def _flag_for(kind: AgentKind, tool_id: str) -> str | None:
    if kind == AgentKind.TABLE:
        return _TABLE_FLAG.get(tool_id)
    return _OTHER_FLAG.get(tool_id)


def _rule_sort(rule: LoadedRule) -> tuple[int, str]:
    return (rule.order, rule.name)
