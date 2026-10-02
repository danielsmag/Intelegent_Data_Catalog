"""Baseline application catalog schema.

After this revision is applied, change tables only in a new revision.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.engine import Connection

from intelegent_data_catalog.persistence.models import Base

revision: str = "20261002_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the core catalog tables."""
    bind: Connection = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Drop the core catalog tables."""
    bind: Connection = op.get_bind()
    Base.metadata.drop_all(bind=bind)
