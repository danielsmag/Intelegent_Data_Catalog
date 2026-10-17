"""OIDC token checks and the dev principal used when auth is off."""

import asyncio
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from dependency_injector import providers
from fastapi import FastAPI

from intelegent_data_catalog.api.container import AppContainer
from intelegent_data_catalog.auth.oidc import KeyLoader, OidcVerifier
from intelegent_data_catalog.auth.principal import Principal
from intelegent_data_catalog.bootstrap import create_app
from intelegent_data_catalog.core.settings import AuthSettings
from intelegent_data_catalog.exceptions import AuthenticationError
from intelegent_data_catalog.persistence.database import CatalogDatabase
from intelegent_data_catalog.persistence.migrate import upgrade_database

_ISSUER: str = "http://issuer"
_AUDIENCE: str = "catalog-ui"


class _PemKeyLoader:
    """Return one public PEM for every token."""

    def __init__(self, public_pem: str) -> None:
        self._public_pem: str = public_pem

    def key_for(self, token: str) -> str:
        """Return the public key. The token is unused."""
        del token
        return self._public_pem


def test_verifier_reads_subject_email_and_admin_role() -> None:
    """A valid token becomes a platform admin when the realm role is present."""
    private: RSAPrivateKey = _private_key()
    verifier: OidcVerifier = _verifier(private, _PemKeyLoader(_public_pem(private)))
    token: str = _token(private, roles=["catalog-admin"])
    principal: Principal = verifier.verify(token)
    assert principal.subject == "user-1"
    assert principal.email == "ada@example.com"
    assert principal.name == "Ada"
    assert principal.is_admin is True


def test_verifier_rejects_expired_and_wrong_audience() -> None:
    """Expiry and audience failures stay authentication errors."""
    private: RSAPrivateKey = _private_key()
    loader: KeyLoader = _PemKeyLoader(_public_pem(private))
    verifier: OidcVerifier = _verifier(private, loader)
    expired: str = _token(private, exp=int(time.time()) - 60)
    wrong_audience: str = _token(private, audience="other")
    with pytest.raises(AuthenticationError):
        verifier.verify(expired)
    with pytest.raises(AuthenticationError):
        verifier.verify(wrong_audience)


def test_disabled_auth_uses_the_dev_principal(tmp_path: Path) -> None:
    """Auth off answers /me as the configured dev admin inside Shared."""
    app: FastAPI
    database: CatalogDatabase
    app, database = _app(tmp_path)
    try:
        response: httpx.Response = asyncio.run(_request(app, "GET", "/api/v1/me"))
        assert response.status_code == 200
        body: dict[str, object] = response.json()
        assert body["subject"] == "dev"
        assert body["is_admin"] is True
        memberships: object = body["memberships"]
        assert isinstance(memberships, list)
        slugs: list[object] = []
        item: object
        for item in memberships:
            assert isinstance(item, dict)
            slugs.append(item.get("slug"))
        assert "shared" in slugs
    finally:
        database.dispose()


def _verifier(private: RSAPrivateKey, loader: KeyLoader) -> OidcVerifier:
    del private
    settings: AuthSettings = AuthSettings(
        enabled=True,
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url="http://jwks",
    )
    return OidcVerifier(settings, loader)


def _private_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _public_pem(private: RSAPrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    public: bytes = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return public.decode("ascii")


def _token(
    private: RSAPrivateKey,
    *,
    roles: list[str] | None = None,
    exp: int | None = None,
    audience: str = _AUDIENCE,
) -> str:
    expires: int = int(time.time()) + 120 if exp is None else exp
    encoded: str = jwt.encode(
        {
            "sub": "user-1",
            "email": "ada@example.com",
            "name": "Ada",
            "aud": audience,
            "iss": _ISSUER,
            "exp": expires,
            "realm_access": {"roles": [] if roles is None else roles},
        },
        private,
        algorithm="RS256",
    )
    return encoded


def _app(tmp_path: Path) -> tuple[FastAPI, CatalogDatabase]:
    url: str = f"sqlite:///{tmp_path / 'catalog.db'}"
    upgrade_database(url)
    database: CatalogDatabase = CatalogDatabase(url)
    container: AppContainer = AppContainer()
    container.database.override(providers.Object(database))
    return create_app(container), database


async def _request(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport: httpx.ASGITransport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path)
