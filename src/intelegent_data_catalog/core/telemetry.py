"""OTLP export of catalog logs and pydantic-ai agent spans."""

from __future__ import annotations

import base64
import logging
import warnings

from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import set_tracer_provider
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

from intelegent_data_catalog.core.logger import LOGGER_NAME
from intelegent_data_catalog.core.settings import ObservabilitySettings


class CatalogTelemetry:
    """Ships logs, spans, and token metrics when observability is enabled.

    A disabled setting leaves the process on its console logger. Export errors
    stay inside the OpenTelemetry batch processors and do not fail API requests.
    """

    def __init__(self, settings: ObservabilitySettings) -> None:
        self._settings: ObservabilitySettings = settings

    def start(self) -> None:
        """Install exporters and agent instrumentation. No-op when disabled."""
        if not self._settings.enabled:
            return
        headers: dict[str, str] = _basic_authorization(
            self._settings.username,
            self._settings.password.get_secret_value(),
        )
        resource: Resource = Resource.create({"service.name": self._settings.service_name})
        self._export_traces(resource, headers)
        self._export_logs(resource, headers)
        self._export_metrics(resource, headers)
        self._instrument_agents()

    def _export_traces(self, resource: Resource, headers: dict[str, str]) -> None:
        exporter: OTLPSpanExporter = OTLPSpanExporter(
            endpoint=_signal_endpoint(self._settings.endpoint, "traces"),
            headers=headers,
        )
        provider: TracerProvider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        set_tracer_provider(provider)

    def _export_logs(self, resource: Resource, headers: dict[str, str]) -> None:
        exporter: OTLPLogExporter = OTLPLogExporter(
            endpoint=_signal_endpoint(self._settings.endpoint, "logs"),
            headers=headers,
        )
        provider: LoggerProvider = LoggerProvider(resource=resource)
        provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(provider)
        logger: logging.Logger = logging.getLogger(LOGGER_NAME)
        logger.addHandler(_logging_handler(logger.level, provider))

    def _export_metrics(self, resource: Resource, headers: dict[str, str]) -> None:
        exporter: OTLPMetricExporter = OTLPMetricExporter(
            endpoint=_signal_endpoint(self._settings.endpoint, "metrics"),
            headers=headers,
        )
        reader: PeriodicExportingMetricReader = PeriodicExportingMetricReader(exporter)
        provider: MeterProvider = MeterProvider(resource=resource, metric_readers=(reader,))
        set_meter_provider(provider)

    def _instrument_agents(self) -> None:
        settings: InstrumentationSettings = InstrumentationSettings(
            include_content=self._settings.include_content,
            include_binary_content=False,
        )
        Agent.instrument_all(settings)


def _signal_endpoint(endpoint: str, signal: str) -> str:
    return f"{endpoint}/v1/{signal}"


def _basic_authorization(username: str, password: str) -> dict[str, str]:
    raw: bytes = f"{username}:{password}".encode()
    token: str = base64.b64encode(raw).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _logging_handler(level: int, provider: LoggerProvider) -> logging.Handler:
    """Build the SDK log handler. Its constructor warns that it is deprecated."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        handler: logging.Handler = LoggingHandler(level=level, logger_provider=provider)
    return handler
