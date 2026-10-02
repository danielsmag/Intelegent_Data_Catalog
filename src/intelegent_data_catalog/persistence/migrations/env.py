"""Alembic migration environment for the catalog schema."""

from alembic import context
from sqlalchemy import Engine, MetaData, create_engine, pool

from intelegent_data_catalog.persistence.models import Base

target_metadata: MetaData = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without opening a connection."""
    url: str | None = context.config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url is not configured")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations on a live connection."""
    url: str | None = context.config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("sqlalchemy.url is not configured")
    connectable: Engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
