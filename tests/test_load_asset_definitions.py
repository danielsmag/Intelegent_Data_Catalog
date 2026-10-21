"""Definition upload keys and store settings. No MinIO connection."""

from pathlib import Path

from pydantic import SecretStr
from scripts.load_asset_definitions import AssetDefinitionUploader

from intelegent_data_catalog.core.settings import (
    AppSettings,
    ObjectStoreSettings,
    load_settings,
    resolve_project_root,
)
from intelegent_data_catalog.storage.object_store import definition_store_settings


def test_example_files_keep_their_folder_layout() -> None:
    """Example files land under spaces/, scopes/, and assets/."""
    settings: AppSettings = load_settings()
    source: Path = resolve_project_root() / "examples" / "asset-definitions"
    uploader: AssetDefinitionUploader = AssetDefinitionUploader(settings, source)
    keys: list[str] = [key for key, _path in uploader.object_keys()]
    assert keys == [
        "asset-definitions/assets/sales/asset_orders.py",
        "asset-definitions/scopes/analytics/scope_finance.py",
        "asset-definitions/spaces/space_analytics.py",
    ]


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
