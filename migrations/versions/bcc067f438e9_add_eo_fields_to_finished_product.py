"""add_eo_fields_to_finished_product

Revision ID: bcc067f438e9
Revises: 653052d620cd
Create Date: 2025-09-02 11:52:03.075282

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'bcc067f438e9'
down_revision = '653052d620cd'
branch_labels = None
depends_on = None


def upgrade():
    # 兼容性：仅在缺少字段时添加，避免重复执行导致的重复列错误
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('finished_product')}

    with op.batch_alter_table('finished_product', schema=None) as batch_op:
        if 'eo' not in existing:
            batch_op.add_column(sa.Column('eo', sa.String(length=100), nullable=True, comment='EO编号'))
        if 'eo_override' not in existing:
            batch_op.add_column(sa.Column('eo_override', sa.Boolean(), nullable=True, comment='EO是否被强制覆盖'))
        if 'usage_eo' not in existing:
            batch_op.add_column(sa.Column('usage_eo', sa.String(length=100), nullable=True, comment='UsageList中的原始EO编号'))
        if 'needs_eo_attention' not in existing:
            batch_op.add_column(sa.Column('needs_eo_attention', sa.Boolean(), nullable=True, comment='是否需要EO注意（UsageList中有EO但用户未输入）'))

    # ### end Alembic commands ###


def downgrade():
    # 兼容性：仅在存在字段时删除
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns('finished_product')}

    with op.batch_alter_table('finished_product', schema=None) as batch_op:
        if 'needs_eo_attention' in existing:
            batch_op.drop_column('needs_eo_attention')
        if 'usage_eo' in existing:
            batch_op.drop_column('usage_eo')
        if 'eo_override' in existing:
            batch_op.drop_column('eo_override')
        if 'eo' in existing:
            batch_op.drop_column('eo')

    # ### end Alembic commands ###
