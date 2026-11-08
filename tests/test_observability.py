"""Observability settings and OTLP startup."""

import logging

import pytest
from pydantic import SecretStr, ValidationError

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import ObservabilitySettings, load_settings
from intelegent_data_catalog.core.telemetry import CatalogTelemetry, OtelLlmCallback
from intelegent_data_catalog.exceptions import SettingsError


class _Exporter:
    def __init__(self, endpoint: str, headers: dict[str, str]) -> None:
        self.endpoint: str = endpoint
        self.headers: dict[str, str] = headers


class _SpanProvider:
    def __init__(self, resource: object | None = None) -> None:
        self.resource: object | None = resource

    def add_span_processor(self, processor: object) -> None:
        return None


class _LogProvider:
    def __init__(self, resource: object | None = None) -> None:
        self.resource: object | None = resource

    def add_log_record_processor(self, processor: object) -> None:
        return None


class _MeterProvider:
    def __init__(
        self,
        resource: object | None = None,
        metric_readers: tuple[object, ...] = (),
    ) -> None:
        self.resource: object | None = resource
        self.metric_readers: tuple[object, ...] = metric_readers


class _ProbeHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        return None


def test_observability_disabled_by_default() -> None:
    """settings.toml leaves export off for a host run."""
    settings: ObservabilitySettings = ObservabilitySettings()
    loaded: ObservabilitySettings = load_settings().observability
    assert settings.enabled is False
    assert loaded.enabled is False
    assert loaded.include_content is True
    assert loaded.password.get_secret_value() == ""


def test_observability_strips_endpoint_slash() -> None:
    """The exporter appends /v1/{signal}, so a trailing slash is removed."""
    settings: ObservabilitySettings = ObservabilitySettings(
        endpoint="http://127.0.0.1:5080/api/default/",
    )
    assert settings.endpoint == "http://127.0.0.1:5080/api/default"


def test_enabled_observability_requires_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """An enabled exporter with an empty password fails settings load."""
    monkeypatch.setenv("CATALOG_OBSERVABILITY__ENABLED", "true")
    with pytest.raises(SettingsError, match="observability password"):
        load_settings()


def test_enabled_observability_rejects_blank_password() -> None:
    """The settings model rejects a blank password when export is on."""
    with pytest.raises(ValidationError, match="observability password"):
        ObservabilitySettings(enabled=True, password=SecretStr("   "))


def test_disabled_start_skips_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """A disabled setting does not install a tracer or instrument agents."""
    calls: list[str] = []

    def _forbid(*_args: object, **_kwargs: object) -> None:
        calls.append("called")

    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.set_tracer_provider",
        _forbid,
    )
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.register_llm_callback",
        _forbid,
    )
    logger: logging.Logger = logging.getLogger(LOGGER_NAME)
    before: int = len(logger.handlers)
    CatalogTelemetry(ObservabilitySettings()).start()
    assert calls == []
    assert len(logger.handlers) == before


def test_enabled_start_exports_and_instruments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabled startup instruments agents with prompt text and ships one log handler."""
    captured: list[OtelLlmCallback] = []

    def _capture(handler: OtelLlmCallback) -> None:
        captured.append(handler)

    def _processor(exporter: _Exporter) -> _Exporter:
        return exporter

    def _handler(level: int = 0, logger_provider: object | None = None) -> logging.Handler:
        probe: _ProbeHandler = _ProbeHandler(level)
        return probe

    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.OTLPSpanExporter", _Exporter)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.OTLPLogExporter", _Exporter)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.OTLPMetricExporter", _Exporter)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.TracerProvider", _SpanProvider)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.LoggerProvider", _LogProvider)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.MeterProvider", _MeterProvider)
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.BatchSpanProcessor", _processor)
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.BatchLogRecordProcessor",
        _processor,
    )
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.PeriodicExportingMetricReader",
        _processor,
    )
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.set_tracer_provider",
        lambda _provider: None,
    )
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.set_logger_provider",
        lambda _provider: None,
    )
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.set_meter_provider",
        lambda _provider: None,
    )
    monkeypatch.setattr("intelegent_data_catalog.core.telemetry.LoggingHandler", _handler)
    monkeypatch.setattr(
        "intelegent_data_catalog.core.telemetry.register_llm_callback",
        _capture,
    )
    logger: logging.Logger = logging.getLogger(LOGGER_NAME)
    before: list[logging.Handler] = list(logger.handlers)
    try:
        CatalogTelemetry(
            ObservabilitySettings(enabled=True, password=SecretStr("secret")),
        ).start()
        added: list[logging.Handler] = [
            handler for handler in logger.handlers if handler not in before
        ]
        assert len(added) == 1
        assert len(captured) == 1
        assert captured[0].include_content is True
        assert captured[0].include_binary_content is False
    finally:
        handler: logging.Handler
        for handler in list(logger.handlers):
            if handler not in before:
                logger.removeHandler(handler)
