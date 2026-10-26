"""
add indexes to operation_logs

Revision ID: ae1b2c3d4e5f
Revises: 854a470a5c03
Create Date: 2025-09-10
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'ae1b2c3d4e5f'
down_revision = '854a470a5c03'
branch_labels = None
depends_on = None


def _has_index(connection, table_name: str, index_name: str) -> bool:
    try:
        inspector = sa.inspect(connection)
        indexes = inspector.get_indexes(table_name)
        return any(ix.get('name') == index_name for ix in indexes)
    except Exception:
        return False


def upgrade():
    bind = op.get_bind()
    conn = bind.connect() if hasattr(bind, 'connect') else bind

    # created_at
    if not _has_index(conn, 'operation_logs', 'ix_operation_logs_created_at'):
        op.create_index('ix_operation_logs_created_at', 'operation_logs', ['created_at'], unique=False)

    # operation_type
    if not _has_index(conn, 'operation_logs', 'ix_operation_logs_operation_type'):
        op.create_index('ix_operation_logs_operation_type', 'operation_logs', ['operation_type'], unique=False)

    # user_id
    if not _has_index(conn, 'operation_logs', 'ix_operation_logs_user_id'):
        op.create_index('ix_operation_logs_user_id', 'operation_logs', ['user_id'], unique=False)


def downgrade():
    # 索引删除前先判断是否存在
    bind = op.get_bind()
    conn = bind.connect() if hasattr(bind, 'connect') else bind

    if _has_index(conn, 'operation_logs', 'ix_operation_logs_user_id'):
        op.drop_index('ix_operation_logs_user_id', table_name='operation_logs')
    if _has_index(conn, 'operation_logs', 'ix_operation_logs_operation_type'):
        op.drop_index('ix_operation_logs_operation_type', table_name='operation_logs')
    if _has_index(conn, 'operation_logs', 'ix_operation_logs_created_at'):
        op.drop_index('ix_operation_logs_created_at', table_name='operation_logs')


