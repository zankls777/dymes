"""add full_name and department to user_info

Revision ID: a1b2c3d4e5f6
Revises: c7d3e9a1b204
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'c7d3e9a1b204'
branch_labels = None
depends_on = None


def upgrade():
    # 兼容：仅在缺失时添加 full_name / department 列（用户姓名、部门）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('user_info')}
    with op.batch_alter_table('user_info', schema=None) as batch_op:
        if 'full_name' not in existing:
            batch_op.add_column(sa.Column('full_name', sa.String(length=80), nullable=True, comment='姓名'))
        if 'department' not in existing:
            batch_op.add_column(sa.Column('department', sa.String(length=80), nullable=True, comment='部门'))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('user_info')}
    with op.batch_alter_table('user_info', schema=None) as batch_op:
        if 'full_name' in existing:
            batch_op.drop_column('full_name')
        if 'department' in existing:
            batch_op.drop_column('department')
