"""GitHub repository snapshot. Skips when the branch head has not changed."""

from typing import ClassVar

from pydantic import JsonValue

from intelegent_data_catalog.assets.definitions.models import GitHubRepoSourceRef
from intelegent_data_catalog.assets.dsl.errors import AssetDefinitionError
from intelegent_data_catalog.assets.dsl.operators.base import (
    Operator,
    OperatorContext,
    OperatorObserver,
    StepSkipped,
    _run_step,
)
from intelegent_data_catalog.assets.dsl.operators.ports import GitHubReader
from intelegent_data_catalog.assets.dsl.operators.render import (
    as_json,
    fence,
    heading,
    join_sections,
    table,
)
from intelegent_data_catalog.sources.github.models import (
    GitHubCommit,
    GitHubRepository,
    GitHubTreeEntry,
)

_TASK_ID: str = "github_repo"


class GitHubRepoSnapshot(Operator):
    """Write repository metadata, the head commit, the tree, and selected text files."""

    task_id: ClassVar[str] = _TASK_ID
    include_readme: bool = True
    include_files: list[str] | None = None

    def execute(self, context: OperatorContext, observer: OperatorObserver | None = None) -> None:
        """Snapshot the branch. An unchanged head is skipped unless the run is forced."""

        def collect() -> None:
            ref: GitHubRepoSourceRef = _source(context)
            reader: GitHubReader = context.github()
            repo: GitHubRepository = reader.repo(ref.owner, ref.repo)
            head: GitHubCommit = reader.branch_head(ref.owner, ref.repo, ref.branch)
            previous: str | None = context.head_sha()
            if not context.force and previous == head.sha:
                raise StepSkipped(f"head unchanged: {head.sha}")
            entries: list[GitHubTreeEntry] = reader.tree(ref.owner, ref.repo, head.sha).entries
            files: dict[str, str] = _files(
                reader,
                ref,
                head.sha,
                entries,
                self.include_readme,
                self.include_files,
            )
            payload: dict[str, JsonValue] = {
                "full_name": repo.full_name,
                "description": repo.description,
                "default_branch": repo.default_branch,
                "branch": ref.branch,
                "private": repo.private,
                "html_url": repo.html_url,
                "stars": repo.stargazers_count,
                "language": repo.language,
                "sha": head.sha,
                "message": head.message,
                "author": head.author,
                "committed_at": head.committed_at,
                "tree": [entry.path for entry in entries if entry.entry_type == "blob"],
                "files": as_json(files),
            }
            context.write_output("repo", _markdown(repo, ref.branch, head, entries, files), payload)
            context.remember_head(head.sha)

        _run_step(observer, self.task_id, collect)


def _source(context: OperatorContext) -> GitHubRepoSourceRef:
    source: object = context.source()
    if not isinstance(source, GitHubRepoSourceRef):
        raise AssetDefinitionError("GitHub operator requires a GitHub repository source")
    return source


def _files(
    reader: GitHubReader,
    ref: GitHubRepoSourceRef,
    sha: str,
    entries: list[GitHubTreeEntry],
    include_readme: bool,
    include_files: list[str] | None,
) -> dict[str, str]:
    wanted: list[str] = []
    if include_readme:
        readme: str | None = _readme_path(entries)
        if readme is not None:
            wanted.append(readme)
    if include_files is not None:
        path: str
        for path in include_files:
            if path not in wanted:
                wanted.append(path)
    found: dict[str, str] = {}
    path_name: str
    for path_name in wanted:
        text: str | None = reader.file_text(ref.owner, ref.repo, path_name, sha)
        if text is not None:
            found[path_name] = text
    return found


def _readme_path(entries: list[GitHubTreeEntry]) -> str | None:
    entry: GitHubTreeEntry
    for entry in entries:
        name: str = entry.path.rsplit("/", 1)[-1].lower()
        if name in {"readme.md", "readme.markdown", "readme.txt", "readme"}:
            return entry.path
    return None


def _markdown(
    repo: GitHubRepository,
    branch: str,
    head: GitHubCommit,
    entries: list[GitHubTreeEntry],
    files: dict[str, str],
) -> str:
    blobs: list[GitHubTreeEntry] = [entry for entry in entries if entry.entry_type == "blob"]
    sections: list[str] = [
        heading(1, repo.full_name),
        repo.description or "",
        table(
            ["field", "value"],
            [
                ["url", repo.html_url],
                ["default branch", repo.default_branch],
                ["snapshot branch", branch],
                ["private", "yes" if repo.private else "no"],
                ["stars", str(repo.stargazers_count)],
                ["language", repo.language or ""],
            ],
        ),
        heading(2, "Head"),
        table(
            ["field", "value"],
            [
                ["sha", head.sha],
                ["author", head.author or ""],
                ["committed", head.committed_at or ""],
                ["message", head.message],
            ],
        ),
        heading(2, f"Tree ({len(blobs)} files)"),
        table(
            ["path", "bytes"],
            [[entry.path, "" if entry.size is None else str(entry.size)] for entry in blobs],
        ),
    ]
    path: str
    body: str
    for path, body in files.items():
        sections.append(heading(2, path))
        language: str = "markdown" if path.lower().endswith(".md") else ""
        sections.append(fence(language, body))
    return join_sections(sections)
