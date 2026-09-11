"""add price_history table

Revision ID: b2c3d4e5f607
Revises: a1b2c3d4e5f6
Create Date: 2026-09-11 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f607'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'price_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('shop_id', postgresql.UUID(as_uuid=True), nullable=True),
        # The enum type already exists in every real database — it is created
        # by the base schema (models.py) together with shops/finance tables.
        sa.Column(
            'marketplace',
            postgresql.ENUM('wb', 'ozon', 'ym', name='marketplace', create_type=False),
            nullable=True,
        ),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_price_history_product_id', 'price_history', ['product_id'])
    op.create_index('ix_price_history_created_at', 'price_history', ['created_at'])
    op.create_index(
        'ix_price_history_product_created', 'price_history', ['product_id', 'created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_price_history_product_created', table_name='price_history')
    op.drop_index('ix_price_history_created_at', table_name='price_history')
    op.drop_index('ix_price_history_product_id', table_name='price_history')
    op.drop_table('price_history')
