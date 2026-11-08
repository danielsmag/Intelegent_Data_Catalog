"""Reject notebook writes that look like secrets."""

import re

_SECRET: re.Pattern[str] = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|BEGIN [A-Z ]*PRIVATE KEY)\s*[:=]\s*\S+"
)


class ArtifactScanner:
    """Blocks a write when the body looks like a credential."""

    def scan(self, content: str) -> str | None:
        """Return a refusal when ``content`` contains a secret. Otherwise none."""
        if _SECRET.search(content) is None:
            return None
        return "refusing to store a secret"
