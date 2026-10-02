"""Reject raw credentials on catalog configuration payloads."""

from urllib.parse import SplitResult, urlsplit

_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "secret_key",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "private_key",
        "access_key",
        "access_key_id",
        "aws_secret_access_key",
        "credential",
        "credentials",
    }
)
_EMBEDDED_MARKERS: tuple[str, ...] = ("password=", "secret=", "token=", "api_key=")


def assert_no_secrets(value: object, field: str) -> None:
    """Raise ValueError when a configuration tree carries secret material."""
    _walk_secrets(value, field)


def reject_endpoint_credentials(endpoint: str) -> str:
    """Reject URLs that embed a username or password."""
    parsed: SplitResult = urlsplit(endpoint)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not include credentials; store a credential_ref instead")
    return endpoint


def _walk_secrets(value: object, field: str) -> None:
    match value:
        case dict() as mapping:
            key: object
            item: object
            for key, item in mapping.items():
                if isinstance(key, str):
                    normalized: str = key.strip().lower().replace("-", "_")
                    if normalized in _SECRET_KEYS:
                        raise ValueError(
                            f"{field} cannot contain secret key {key!r}; "
                            "store a credential_ref instead"
                        )
                _walk_secrets(item, field)
        case list() as items:
            entry: object
            for entry in items:
                _walk_secrets(entry, field)
        case str() as text_value:
            lowered: str = text_value.lower()
            marker: str
            for marker in _EMBEDDED_MARKERS:
                if marker in lowered:
                    raise ValueError(
                        f"{field} cannot contain embedded credentials; "
                        "store a credential_ref instead"
                    )
        case _:
            return
