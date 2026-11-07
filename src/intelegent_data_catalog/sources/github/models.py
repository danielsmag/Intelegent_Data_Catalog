"""GitHub REST payloads. Extra fields are ignored."""

from pydantic import BaseModel, ConfigDict, Field


class GitHubRepository(BaseModel):
    """GET /repos/{owner}/{repo}."""

    model_config = ConfigDict(extra="ignore")

    full_name: str
    description: str | None = None
    default_branch: str
    private: bool = False
    html_url: str
    stargazers_count: int = 0
    language: str | None = None


class GitHubCommit(BaseModel):
    """Head commit of one ref."""

    model_config = ConfigDict(extra="ignore")

    sha: str
    message: str
    author: str | None = None
    committed_at: str | None = None


class GitHubTreeEntry(BaseModel):
    """One path in a git tree."""

    model_config = ConfigDict(extra="ignore")

    path: str
    entry_type: str = Field(alias="type")
    sha: str
    size: int | None = None


class GitHubTree(BaseModel):
    """Recursive tree for one commit."""

    model_config = ConfigDict(extra="ignore")

    sha: str
    truncated: bool = False
    entries: list[GitHubTreeEntry]
