"""Add new fine-grained permission columns to permission_info

Revision ID: 8b2f1a9a6f3a
Revises: 73c77a37c34e
Create Date: 2025-08-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8b2f1a9a6f3a'
down_revision = '73c77a37c34e'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('permission_info', schema=None) as batch_op:
        # SubList 扩展
        batch_op.add_column(sa.Column('can_sub_list_show_differences', sa.Boolean(), nullable=True, server_default=sa.text('0')))

        # 车种/成品 批量删除独立权限
        batch_op.add_column(sa.Column('can_vehicle_types_batch_delete', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_products_batch_delete', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_products_batch_add', sa.Boolean(), nullable=True, server_default=sa.text('0')))

        # 用户与权限管理页面按钮权限
        batch_op.add_column(sa.Column('can_user_mgmt_add_user', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_user_mgmt_change_password', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_user_mgmt_reset_password', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_user_mgmt_manage_permissions', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_user_mgmt_delete_user', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        # 车种扩展
        batch_op.add_column(sa.Column('can_vehicle_types_batch_add', sa.Boolean(), nullable=True, server_default=sa.text('0')))
        batch_op.add_column(sa.Column('can_vehicle_types_import_excel', sa.Boolean(), nullable=True, server_default=sa.text('0')))

    # 清除server_default 以保持应用层默认
    with op.batch_alter_table('permission_info', schema=None) as batch_op:
        batch_op.alter_column('can_sub_list_show_differences', server_default=None)
        batch_op.alter_column('can_vehicle_types_batch_delete', server_default=None)
        batch_op.alter_column('can_products_batch_delete', server_default=None)
        batch_op.alter_column('can_products_batch_add', server_default=None)
        batch_op.alter_column('can_user_mgmt_add_user', server_default=None)
        batch_op.alter_column('can_user_mgmt_change_password', server_default=None)
        batch_op.alter_column('can_user_mgmt_reset_password', server_default=None)
        batch_op.alter_column('can_user_mgmt_manage_permissions', server_default=None)
        batch_op.alter_column('can_user_mgmt_delete_user', server_default=None)
        batch_op.alter_column('can_vehicle_types_batch_add', server_default=None)
        batch_op.alter_column('can_vehicle_types_import_excel', server_default=None)


def downgrade():
    with op.batch_alter_table('permission_info', schema=None) as batch_op:
        batch_op.drop_column('can_user_mgmt_delete_user')
        batch_op.drop_column('can_user_mgmt_manage_permissions')
        batch_op.drop_column('can_user_mgmt_reset_password')
        batch_op.drop_column('can_user_mgmt_change_password')
        batch_op.drop_column('can_user_mgmt_add_user')
        batch_op.drop_column('can_products_batch_delete')
        batch_op.drop_column('can_vehicle_types_batch_delete')
        batch_op.drop_column('can_sub_list_show_differences')


