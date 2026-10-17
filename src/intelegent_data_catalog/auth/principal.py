"""Authenticated caller taken from an OIDC token or the dev settings."""

from dataclasses import dataclass

from intelegent_data_catalog.core.settings import AuthSettings


@dataclass(frozen=True, slots=True)
class Principal:
    """Identity claims. ``is_admin`` is the platform role, not a workspace role."""

    subject: str
    email: str | None
    name: str
    roles: frozenset[str]
    is_admin: bool

    @staticmethod
    def dev(settings: AuthSettings) -> Principal:
        """Build the principal used when auth is disabled."""
        roles: frozenset[str] = frozenset()
        if settings.dev_is_admin:
            roles = frozenset({settings.admin_role})
        email: str | None = settings.dev_email.strip() or None
        return Principal(
            subject=settings.dev_subject,
            email=email,
            name=settings.dev_name,
            roles=roles,
            is_admin=settings.dev_is_admin,
        )
