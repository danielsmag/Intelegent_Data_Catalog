"""Composition root."""

from dependency_injector import containers, providers

from intelegent_data_catalog.core.settings import AppSettings


class Container(containers.DeclarativeContainer):
    """Wires shared application objects."""

    settings: providers.Singleton[AppSettings] = providers.Singleton(AppSettings)
