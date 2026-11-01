"""API shapes for the markdown files beside one asset declaration."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ToolId = Literal[
    "metadata",
    "profile",
    "lineage",
    "graph",
    "source",
    "outputs",
    "table_links",
]


class AgentCardRead(BaseModel):
    """``AGENTS.md`` in the asset directory. Empty when the file is absent."""

    model_config = ConfigDict(extra="forbid")

    path: str = "AGENTS.md"
    present: bool
    updated_at: datetime | None = None


class PromptRead(BaseModel):
    """One prompt file, without its markdown body."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str
    enabled: bool
    active: bool
    updated_at: datetime | None = None


class RuleRead(BaseModel):
    """One rule file, without its markdown body."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str
    enabled: bool
    order: int
    updated_at: datetime | None = None


class SkillRead(BaseModel):
    """One skill directory, without its markdown body."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    description: str
    enabled: bool
    tools: list[str]
    updated_at: datetime | None = None


class ReferenceRead(BaseModel):
    """One file under a skill's ``references`` directory."""

    model_config = ConfigDict(extra="forbid")

    name: str
    body: str


class AgentPackRead(BaseModel):
    """Listing for the asset page. Bodies stay on the detail routes."""

    model_config = ConfigDict(extra="forbid")

    prefix: str | None
    writable: bool
    message: str | None = None
    agent: AgentCardRead
    prompts: list[PromptRead]
    rules: list[RuleRead]
    skills: list[SkillRead]


class PromptDetail(PromptRead):
    """One prompt, including the markdown body."""

    body: str


class RuleDetail(RuleRead):
    """One rule, including the markdown body."""

    body: str


class SkillDetail(SkillRead):
    """One skill, including ``SKILL.md`` and reference files."""

    body: str
    references: list[ReferenceRead]


class AgentDetail(AgentCardRead):
    """``AGENTS.md`` text."""

    body: str


class PromptWrite(BaseModel):
    """Replace one prompt file."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    active: bool = False
    body: str = Field(default="", max_length=24_000)


class RuleWrite(BaseModel):
    """Replace one rule file."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    enabled: bool = True
    order: int = Field(default=0, ge=0, le=10_000)
    body: str = Field(default="", max_length=24_000)


class SkillWrite(BaseModel):
    """Replace one skill's ``SKILL.md``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1024)
    enabled: bool = True
    tools: list[ToolId] = Field(default_factory=list)
    body: str = Field(default="", max_length=24_000)


class AgentWrite(BaseModel):
    """Replace ``AGENTS.md``. The harness reads this file as written."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(default="", max_length=24_000)


class MemoryFileRead(BaseModel):
    """One notebook file, without its markdown body."""

    model_config = ConfigDict(extra="forbid")

    name: str
    updated_at: datetime | None = None


class MemoryFileDetail(MemoryFileRead):
    """One notebook file the agent wrote. The page only displays it."""

    body: str
    updated_by: str | None = None
