"""Apply catalog schema migrations."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from intelegent_data_catalog.exceptions import PersistenceUnavailableError

_MIGRATIONS: Path = Path(__file__).resolve().parent / "migrations"


def upgrade_database(url: str) -> None:
    """Upgrade the catalog database to the current schema."""
    config: Config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    try:
        command.upgrade(config, "head")
    except PersistenceUnavailableError:
        raise
    except Exception as err:
        raise PersistenceUnavailableError("catalog database migration failed") from err
