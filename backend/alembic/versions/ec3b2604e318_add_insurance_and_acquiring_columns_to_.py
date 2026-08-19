"""Add insurance and acquiring columns to sales

Revision ID: ec3b2604e318
Revises: b0bc99cd62de
Create Date: 2026-08-19 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ec3b2604e318"
down_revision: Union[str, None] = "b0bc99cd62de"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sales", sa.Column("insurance", sa.Numeric(12, 2), nullable=True))
    op.add_column("sales", sa.Column("acquiring", sa.Numeric(12, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("sales", "acquiring")
    op.drop_column("sales", "insurance")
