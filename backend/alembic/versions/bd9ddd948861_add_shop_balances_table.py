"""add shop_balances table

Revision ID: bd9ddd948861
Revises: ec3b2604e318
Create Date: 2026-08-23 15:09:40.477356

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'bd9ddd948861'
down_revision = 'ec3b2604e318'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table('shop_balances'):
        return
    op.create_table(
        'shop_balances',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('shop_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('balance', sa.Numeric(14, 2), nullable=False),
        sa.Column('payout_at', sa.DateTime(), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('shop_id', name='uix_shop_balance_shop_id'),
    )


def downgrade() -> None:
    op.drop_table('shop_balances')
