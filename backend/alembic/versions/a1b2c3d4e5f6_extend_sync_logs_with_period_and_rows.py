"""extend sync_logs with period, rows and timing fields

Revision ID: a1b2c3d4e5f6
Revises: 145c1900a03e
Create Date: 2026-09-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '145c1900a03e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sync_logs', sa.Column('date_from', sa.Date(), nullable=True))
    op.add_column('sync_logs', sa.Column('date_to', sa.Date(), nullable=True))
    op.add_column('sync_logs', sa.Column('rows_received', sa.Integer(), nullable=True))
    op.add_column('sync_logs', sa.Column('rows_saved', sa.Integer(), nullable=True))
    op.add_column('sync_logs', sa.Column('started_at', sa.DateTime(), nullable=True))
    op.add_column(
        'sync_logs',
        sa.Column(
            'is_partial',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column('sync_logs', 'is_partial', server_default=None)


def downgrade() -> None:
    op.drop_column('sync_logs', 'is_partial')
    op.drop_column('sync_logs', 'started_at')
    op.drop_column('sync_logs', 'rows_saved')
    op.drop_column('sync_logs', 'rows_received')
    op.drop_column('sync_logs', 'date_to')
    op.drop_column('sync_logs', 'date_from')
