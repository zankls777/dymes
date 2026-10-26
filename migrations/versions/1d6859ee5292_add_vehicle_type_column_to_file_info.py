"""add_vehicle_type_column_to_file_info

Revision ID: 1d6859ee5292
Revises: 94220eb21fd4
Create Date: 2025-09-07 14:37:57.293073

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '1d6859ee5292'
down_revision = '94220eb21fd4'
branch_labels = None
depends_on = None


def upgrade():
    # 兼容：仅在缺失时添加 vehicle_type 列
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('file_info')}
    if 'vehicle_type' not in existing:
        with op.batch_alter_table('file_info', schema=None) as batch_op:
            batch_op.add_column(sa.Column('vehicle_type', sa.String(length=100), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # 兼容：仅在存在时删除 vehicle_type 列
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('file_info')}
    if 'vehicle_type' in existing:
        with op.batch_alter_table('file_info', schema=None) as batch_op:
            batch_op.drop_column('vehicle_type')

    # ### end Alembic commands ###
