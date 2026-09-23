"""Settings load from defaults and the environment."""

import pytest

from intelegent_data_catalog.core.container import Container
from intelegent_data_catalog.core.settings import AppSettings


def test_default_settings_app_name() -> None:
    settings: AppSettings = AppSettings()
    assert settings.app_name == "intelegent-data-catalog"
    assert settings.debug is False


def test_container_provides_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALOG_APP_NAME", "warehouse")
    container: Container = Container()
    settings: AppSettings = container.settings()
    assert settings.app_name == "warehouse"
