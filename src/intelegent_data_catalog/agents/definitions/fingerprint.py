"""Stable hash of one declaration. Unchanged hashes skip re-apply."""

import hashlib

from pydantic import BaseModel


def fingerprint(spec: BaseModel) -> str:
    """Return a sha256 hex digest of the declaration JSON."""
    payload: str = spec.model_dump_json()
    return hashlib.sha256(payload.encode()).hexdigest()


def declaration_payload(spec: BaseModel) -> dict[str, object]:
    """Return JSON-ready declaration data for the item row."""
    dumped: dict[str, object] = {}
    for key, value in spec.model_dump(mode="json").items():
        dumped[str(key)] = value
    return dumped
