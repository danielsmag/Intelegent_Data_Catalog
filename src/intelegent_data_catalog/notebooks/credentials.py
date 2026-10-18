"""Resolve ``credential_ref`` values without putting secrets in the registry."""

import os
from dataclasses import dataclass
from typing import Protocol

from intelegent_data_catalog.core.settings import CredentialEntry

_ENV_PREFIX: str = "CATALOG_CREDENTIALS__"


@dataclass(frozen=True, slots=True)
class Credential:
    """Username and password handed to a kernel, never to the browser."""

    user: str
    password: str


class CredentialResolver(Protocol):
    """Looks up one credential reference."""

    def resolve(self, credential_ref: str | None) -> Credential | None:
        """Return credentials, or None when the reference is missing."""


class EnvCredentialResolver:
    """Settings dict first, then ``env:VAR`` and ``CATALOG_CREDENTIALS__<REF>__``."""

    def __init__(self, credentials: dict[str, CredentialEntry]) -> None:
        self._credentials: dict[str, CredentialEntry] = {
            key.lower(): value for key, value in credentials.items()
        }

    def resolve(self, credential_ref: str | None) -> Credential | None:
        """Return credentials for a reference. An empty reference resolves to None."""
        if credential_ref is None:
            return None
        ref: str = credential_ref.strip()
        if ref == "":
            return None
        if ref.lower().startswith("env:"):
            return self._from_env_var(ref.split(":", 1)[1])
        configured: Credential | None = self._from_settings(ref)
        if configured is not None:
            return configured
        return self._from_prefixed_env(ref)

    def _from_settings(self, ref: str) -> Credential | None:
        entry: CredentialEntry | None = self._credentials.get(ref.lower())
        if entry is None:
            return None
        password: str = entry.password.get_secret_value()
        if entry.user.strip() == "" or password == "":
            return None
        return Credential(user=entry.user, password=password)

    def _from_env_var(self, variable: str) -> Credential | None:
        if variable == "":
            return None
        password: str | None = os.environ.get(variable)
        if password is None or password == "":
            return None
        return Credential(user="", password=password)

    def _from_prefixed_env(self, ref: str) -> Credential | None:
        found_user: str = ""
        found_password: str = ""
        key: str
        value: str
        for key, value in os.environ.items():
            if not key.startswith(_ENV_PREFIX):
                continue
            name, separator, field = key[len(_ENV_PREFIX) :].rpartition("__")
            if separator == "" or name.lower() != ref.lower():
                continue
            if field.lower() == "user":
                found_user = value
            elif field.lower() == "password":
                found_password = value
        if found_user.strip() == "" or found_password == "":
            return None
        return Credential(user=found_user, password=found_password)
