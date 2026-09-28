"""Process logger configured from settings."""

import logging
import sys
from pathlib import Path
from typing import TextIO

from intelegent_data_catalog.core.settings import LoggingSettings
from intelegent_data_catalog.exceptions import LoggingError

LOGGER_NAME: str = "intelegent_data_catalog"


class CatalogLogger:
    """Builds the package logger from logging settings."""

    def __init__(
        self,
        logging_settings: LoggingSettings,
        *,
        debug: bool,
        project_root: Path,
    ) -> None:
        self._logging_settings: LoggingSettings = logging_settings
        self._debug: bool = debug
        self._project_root: Path = project_root

    def configure(self) -> logging.Logger:
        """Replace handlers on the package logger and return it."""
        logger: logging.Logger = logging.getLogger(LOGGER_NAME)
        self._close_handlers(logger)
        effective_level: str = "DEBUG" if self._debug else self._logging_settings.level
        level_number: int = logging.getLevelNamesMapping()[effective_level]
        logger.setLevel(level_number)
        logger.propagate = False
        formatter: logging.Formatter = logging.Formatter(
            fmt=self._format_string(),
            datefmt=self._logging_settings.date_format,
        )
        if self._logging_settings.log_to_console:
            console_handler: logging.StreamHandler[TextIO] = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(level_number)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
        if self._logging_settings.log_to_file:
            logger.addHandler(self._file_handler(level_number, formatter))
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.info(
            "logger configured level=%s console=%s file=%s",
            effective_level,
            self._logging_settings.log_to_console,
            self._logging_settings.log_to_file,
        )
        logger.debug(
            "logger options format=%s date_format=%s log_file=%s",
            self._format_string(),
            self._logging_settings.date_format,
            self._log_path(),
        )
        return logger

    def _format_string(self) -> str:
        if self._debug:
            return self._logging_settings.debug_format
        return self._logging_settings.format

    def _log_path(self) -> Path:
        configured: Path = Path(self._logging_settings.log_file)
        if configured.is_absolute():
            return configured
        return self._project_root / configured

    def _file_handler(
        self,
        level_number: int,
        formatter: logging.Formatter,
    ) -> logging.FileHandler:
        log_path: Path = self._log_path()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler: logging.FileHandler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError as err:
            raise LoggingError(f"cannot open log file {log_path}") from err
        file_handler.setLevel(level_number)
        file_handler.setFormatter(formatter)
        return file_handler

    @staticmethod
    def _close_handlers(logger: logging.Logger) -> None:
        handler: logging.Handler
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def configure_logging(
    settings_debug: bool,
    logging_settings: LoggingSettings,
    project_root: Path,
) -> logging.Logger:
    """Configure and return the package logger."""
    catalog_logger: CatalogLogger = CatalogLogger(
        logging_settings,
        debug=settings_debug,
        project_root=project_root,
    )
    return catalog_logger.configure()
