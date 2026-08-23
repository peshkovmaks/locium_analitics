"""add is_supported flag to shop_balances

Revision ID: 98f3202cf575
Revises: bd9ddd948861
Create Date: 2026-08-23 16:48:53.384000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '98f3202cf575'
down_revision = 'bd9ddd948861'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c['name'] for c in inspector.get_columns('shop_balances')]
    if 'is_supported' not in columns:
        op.add_column(
            'shop_balances',
            sa.Column('is_supported', sa.Boolean(), nullable=False, server_default='true'),
        )


def downgrade() -> None:
    op.drop_column('shop_balances', 'is_supported')
