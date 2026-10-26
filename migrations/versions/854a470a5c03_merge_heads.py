"""merge heads

Revision ID: 854a470a5c03
Revises: 8b2f1a9a6f3a, create_material_summary_table
Create Date: 2025-08-18 17:20:48.704261

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '854a470a5c03'
down_revision = ('8b2f1a9a6f3a', 'create_material_summary_table')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
