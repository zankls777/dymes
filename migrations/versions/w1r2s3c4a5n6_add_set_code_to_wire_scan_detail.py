"""add set_code to wire_scan_detail

为 wire_scan_detail 快照表增加 set_code（图纸编号）字段，
使同图纸兄弟回路兜底查询能按 SetCode 精确过滤，避免返回不同图纸的无关回路。

Revision ID: w1r2s3c4a5n6
Revises: a1b2c3d4e5f6
Create Date: 2026-11-09
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'w1r2s3c4a5n6'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('wire_scan_detail', schema=None) as batch_op:
        batch_op.add_column(sa.Column('set_code', sa.String(length=50), nullable=True,
                                      comment='图纸编号 SetCode（快照，用于同图纸兄弟回路过滤）'))
        batch_op.create_index('idx_wsd_lot_setcode', ['lot_no', 'set_code'])


def downgrade():
    with op.batch_alter_table('wire_scan_detail', schema=None) as batch_op:
        batch_op.drop_index('idx_wsd_lot_setcode')
        batch_op.drop_column('set_code')
