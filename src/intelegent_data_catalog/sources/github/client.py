"""Read-only GitHub REST calls for one repository snapshot."""

import base64
import logging
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from intelegent_data_catalog.core.settings import GitHubSettings
from intelegent_data_catalog.exceptions import GitHubNotFoundError, GitHubUnavailableError
from intelegent_data_catalog.sources.github.models import (
    GitHubCommit,
    GitHubRepository,
    GitHubTree,
    GitHubTreeEntry,
)

_API_VERSION: str = "2022-11-28"


class GitHubClient:
    """Calls api.github.com. A missing token reads public repositories."""

    def __init__(
        self,
        settings: GitHubSettings,
        http_client: httpx.Client,
        logger: logging.Logger,
    ) -> None:
        self._settings: GitHubSettings = settings
        self._http: httpx.Client = http_client
        self._logger: logging.Logger = logger

    def repo(self, owner: str, name: str) -> GitHubRepository:
        """Return repository metadata. A 404 raises ``GitHubNotFoundError``."""
        payload: dict[str, object] = self._get(f"/repos/{_segment(owner)}/{_segment(name)}")
        return _parse(GitHubRepository, payload, "repository")

    def branch_head(self, owner: str, name: str, branch: str) -> GitHubCommit:
        """Return the commit currently at ``branch``."""
        ref: str = quote(branch, safe="")
        payload: dict[str, object] = self._get(
            f"/repos/{_segment(owner)}/{_segment(name)}/commits/{ref}",
        )
        sha: object = payload.get("sha")
        commit: object = payload.get("commit")
        if not isinstance(sha, str) or not isinstance(commit, dict):
            raise GitHubUnavailableError("GitHub commit payload was unexpected")
        message: object = commit.get("message")
        author: object = commit.get("author")
        author_name: str | None = None
        committed_at: str | None = None
        if isinstance(author, dict):
            raw_name: object = author.get("name")
            raw_date: object = author.get("date")
            if isinstance(raw_name, str):
                author_name = raw_name
            if isinstance(raw_date, str):
                committed_at = raw_date
        if not isinstance(message, str):
            message = ""
        return GitHubCommit(
            sha=sha,
            message=message,
            author=author_name,
            committed_at=committed_at,
        )

    def tree(self, owner: str, name: str, sha: str) -> GitHubTree:
        """Return the recursive tree for one commit."""
        payload: dict[str, object] = self._get(
            f"/repos/{_segment(owner)}/{_segment(name)}/git/trees/{quote(sha, safe='')}",
            params={"recursive": "1"},
        )
        raw_entries: object = payload.get("tree")
        entries: list[GitHubTreeEntry] = []
        if isinstance(raw_entries, list):
            item: object
            for item in raw_entries:
                if not isinstance(item, dict):
                    continue
                try:
                    entries.append(GitHubTreeEntry.model_validate(item))
                except ValidationError:
                    continue
        tree_sha: object = payload.get("sha")
        truncated: object = payload.get("truncated")
        return GitHubTree(
            sha=tree_sha if isinstance(tree_sha, str) else sha,
            truncated=truncated is True,
            entries=entries,
        )

    def file_text(self, owner: str, name: str, path: str, ref: str) -> str | None:
        """Return UTF-8 file text, or none when the file is binary or too large."""
        encoded_path: str = "/".join(quote(part, safe="") for part in path.split("/"))
        payload: dict[str, object] = self._get(
            f"/repos/{_segment(owner)}/{_segment(name)}/contents/{encoded_path}",
            params={"ref": ref},
        )
        size: object = payload.get("size")
        if isinstance(size, int) and size > self._settings.max_file_bytes:
            return None
        encoding: object = payload.get("encoding")
        content: object = payload.get("content")
        if encoding != "base64" or not isinstance(content, str):
            return None
        try:
            raw: bytes = base64.b64decode(content, validate=False)
        except ValueError:
            return None
        if len(raw) > self._settings.max_file_bytes:
            return None
        if b"\x00" in raw:
            return None
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, object]:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "intelegent-data-catalog",
        }
        token: str = self._settings.token.get_secret_value().strip()
        if token != "":
            headers["Authorization"] = f"Bearer {token}"
        try:
            response: httpx.Response = self._http.get(
                f"{self._settings.api_url}{path}",
                headers=headers,
                params=params,
                timeout=self._settings.timeout_seconds,
            )
        except httpx.HTTPError as err:
            self._logger.warning("github request failed path=%s", path)
            raise GitHubUnavailableError("GitHub is unavailable") from err
        if response.status_code == 404:
            owner, repo = _owner_repo(path)
            raise GitHubNotFoundError(owner, repo)
        if not response.is_success:
            self._logger.warning("github status=%s path=%s", response.status_code, path)
            raise GitHubUnavailableError(f"GitHub request failed: HTTP {response.status_code}")
        try:
            payload: object = response.json()
        except ValueError as err:
            raise GitHubUnavailableError("GitHub returned invalid JSON") from err
        if not isinstance(payload, dict):
            raise GitHubUnavailableError("GitHub returned an unexpected payload")
        found: dict[str, object] = {}
        key: object
        value: object
        for key, value in payload.items():
            found[str(key)] = value
        return found


def _segment(value: str) -> str:
    return quote(value, safe="")


def _owner_repo(path: str) -> tuple[str, str]:
    parts: list[str] = [part for part in path.split("/") if part != ""]
    if len(parts) >= 3 and parts[0] == "repos":
        return parts[1], parts[2]
    return "unknown", "unknown"


def _parse[ModelT: BaseModel](
    model: type[ModelT],
    payload: dict[str, object],
    label: str,
) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as err:
        raise GitHubUnavailableError(f"GitHub {label} payload was unexpected") from err
