"""CLI entrypoint."""

from intelegent_data_catalog.core.container import Container
from intelegent_data_catalog.core.settings import AppSettings


def main() -> None:
    """Load settings and print the application name."""
    container: Container = Container()
    settings: AppSettings = container.settings()
    print(settings.app_name)


if __name__ == "__main__":
    main()
