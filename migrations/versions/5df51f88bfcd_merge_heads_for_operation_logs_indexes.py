"""merge heads for operation_logs indexes

Revision ID: 5df51f88bfcd
Revises: 1d6859ee5292, ae1b2c3d4e5f
Create Date: 2025-09-10 23:35:30.017944

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5df51f88bfcd'
down_revision = ('1d6859ee5292', 'ae1b2c3d4e5f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
