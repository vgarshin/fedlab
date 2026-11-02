"""teams.review_flags: manipulation hints from stage 2 / 3

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('teams', sa.Column('review_flags', sa.JSON(), nullable=False, server_default='[]'))


def downgrade() -> None:
    op.drop_column('teams', 'review_flags')
