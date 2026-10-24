"""Definition store settings. No MinIO connection."""

from pydantic import SecretStr

from intelegent_data_catalog.core.settings import ObjectStoreSettings
from intelegent_data_catalog.storage.object_store import definition_store_settings


def test_definition_endpoint_overrides_the_workspace_store() -> None:
    """A definition endpoint is used even when workspace files stay in memory."""
    base: ObjectStoreSettings = ObjectStoreSettings()
    chosen: ObjectStoreSettings = definition_store_settings(
        base,
        endpoint="http://127.0.0.1:9000",
        access_key="polaris_root",
        secret_key=SecretStr("polaris_pass"),
        region="us-west-2",
        bucket="workspace",
    )
    assert base.endpoint == ""
    assert chosen.endpoint == "http://127.0.0.1:9000"
    assert chosen.bucket == "workspace"
    assert chosen.secret_key.get_secret_value() == "polaris_pass"


def test_blank_definition_fields_use_the_workspace_store() -> None:
    """Blank definition credentials fall back to the workspace object store."""
    base: ObjectStoreSettings = ObjectStoreSettings(
        endpoint="http://minio:9000",
        access_key="root",
        secret_key=SecretStr("secret"),
        bucket="workspace",
    )
    chosen: ObjectStoreSettings = definition_store_settings(
        base,
        endpoint="",
        access_key="",
        secret_key=SecretStr(""),
        region="",
        bucket="",
    )
    assert chosen.endpoint == "http://minio:9000"
    assert chosen.access_key == "root"
    assert chosen.secret_key.get_secret_value() == "secret"
    assert chosen.bucket == "workspace"
