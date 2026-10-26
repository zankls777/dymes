"""add inbound_date to material_inbound_daily

Revision ID: c7d3e9a1b204
Revises: 5df51f88bfcd
Create Date: 2026-09-26
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7d3e9a1b204'
down_revision = '5df51f88bfcd'
branch_labels = None
depends_on = None


def upgrade():
    # 兼容：仅在缺失时添加 inbound_date 列（入库日期，操作员登录时填写）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('material_inbound_daily')}
    if 'inbound_date' not in existing:
        with op.batch_alter_table('material_inbound_daily', schema=None) as batch_op:
            batch_op.add_column(sa.Column('inbound_date', sa.Date(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('material_inbound_daily')}
    if 'inbound_date' in existing:
        with op.batch_alter_table('material_inbound_daily', schema=None) as batch_op:
            batch_op.drop_column('inbound_date')
