"""CLI entrypoint."""

import logging
import sys

from intelegent_data_catalog.core.settings import AppSettings
from intelegent_data_catalog.exceptions import CatalogError


def main() -> None:
    """Start the scan scheduler using the interval from settings."""
    try:
        settings: AppSettings = AppSettings()
        logger: logging.Logger = logging.getLogger(__name__)
    except CatalogError as err:
        print(f"startup failed: {err}", file=sys.stderr)
        raise SystemExit(1) from err
    logger.info("starting app=%s", settings.app.name)
    try:
        pass
    except KeyboardInterrupt:
        logger.info("interrupted")


if __name__ == "__main__":
    main()
