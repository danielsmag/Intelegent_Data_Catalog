"""OIDC access-token checks against a JWKS endpoint."""

from typing import Protocol, cast

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt import PyJWKClient

from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.core.settings import AuthSettings
from intelegent_data_catalog.exceptions import AuthenticationError


class KeyLoader(Protocol):
    """Supplies the public key that signed one token."""

    def key_for(self, token: str) -> str:
        """Return a PEM or JWK key for ``token``."""
        ...


class JwksKeyLoader:
    """Load signing keys from a remote JWKS document. PyJWT caches the keys."""

    def __init__(self, jwks_url: str) -> None:
        self._client: PyJWKClient = PyJWKClient(jwks_url, cache_keys=True)

    def key_for(self, token: str) -> str:
        """Return the PEM public key that signed ``token``."""
        try:
            key: object = self._client.get_signing_key_from_jwt(token).key
        except jwt.PyJWTError as err:
            raise AuthenticationError("invalid token") from err
        if isinstance(key, str):
            return key
        if isinstance(key, RSAPublicKey):
            pem: bytes = key.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            return pem.decode("ascii")
        raise AuthenticationError("invalid token")


class OidcVerifier:
    """Validate issuer, audience, and expiry, then read the platform admin role."""

    def __init__(self, settings: AuthSettings, keys: KeyLoader | None = None) -> None:
        self._settings: AuthSettings = settings
        self._keys: KeyLoader = keys if keys is not None else JwksKeyLoader(settings.jwks_url)

    def verify(self, token: str) -> Principal:
        """Return the principal or raise ``AuthenticationError``."""
        try:
            key: str = self._keys.key_for(token)
            decoded: object = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._settings.audience,
                issuer=self._settings.issuer,
            )
        except jwt.PyJWTError as err:
            raise AuthenticationError("invalid token") from err
        if not isinstance(decoded, dict):
            raise AuthenticationError("invalid token")
        claims: dict[str, object] = cast(dict[str, object], decoded)
        subject: str = _text(claims.get("sub"))
        if subject == "":
            raise AuthenticationError("invalid token")
        email_raw: str = _text(claims.get("email"))
        email: str | None = email_raw if email_raw != "" else None
        name: str = _text(claims.get("name"))
        if name == "":
            name = _text(claims.get("preferred_username"))
        if name == "":
            name = subject
        roles: frozenset[str] = _roles(claims.get("realm_access"))
        return Principal(
            subject=subject,
            email=email,
            name=name,
            roles=roles,
            is_admin=self._settings.admin_role in roles,
        )


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _roles(realm_access: object) -> frozenset[str]:
    if not isinstance(realm_access, dict):
        return frozenset()
    raw: object = cast(dict[str, object], realm_access).get("roles")
    if not isinstance(raw, list):
        return frozenset()
    found: set[str] = set()
    item: object
    for item in cast(list[object], raw):
        if isinstance(item, str) and item != "":
            found.add(item)
    return frozenset(found)
