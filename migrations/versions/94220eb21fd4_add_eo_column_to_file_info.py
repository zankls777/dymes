"""add_eo_column_to_file_info

Revision ID: 94220eb21fd4
Revises: bcc067f438e9
Create Date: 2025-09-07 14:35:46.310543

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '94220eb21fd4'
down_revision = 'bcc067f438e9'
branch_labels = None
depends_on = None


def upgrade():
    # 兼容：仅在缺失时添加 eo 列
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('file_info')}
    if 'eo' not in existing:
        with op.batch_alter_table('file_info', schema=None) as batch_op:
            batch_op.add_column(sa.Column('eo', sa.String(length=20), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # 兼容：仅在存在时删除 eo 列
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('file_info')}
    if 'eo' in existing:
        with op.batch_alter_table('file_info', schema=None) as batch_op:
            batch_op.drop_column('eo')

    # ### end Alembic commands ###
