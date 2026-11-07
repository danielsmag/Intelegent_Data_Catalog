"""GitHub REST client."""

from intelegent_data_catalog.sources.github.client import GitHubClient
from intelegent_data_catalog.sources.github.models import (
    GitHubCommit,
    GitHubRepository,
    GitHubTree,
    GitHubTreeEntry,
)

__all__ = [
    "GitHubClient",
    "GitHubCommit",
    "GitHubRepository",
    "GitHubTree",
    "GitHubTreeEntry",
]
