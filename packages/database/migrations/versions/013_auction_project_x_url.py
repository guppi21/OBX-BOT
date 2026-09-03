"""Add project_x_url and profile preview metadata to auctions table

Revision ID: 013_auction_project_x_url
Revises: 012_task_required_actions
Create Date: 2026-09-03 06:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '013_auction_project_x_url'
down_revision: Union[str, None] = '012_task_required_actions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'auctions',
        sa.Column('project_x_url', sa.String(length=1024), nullable=True),
    )
    op.add_column(
        'auctions',
        sa.Column('preview_x_handle', sa.String(length=128), nullable=True),
    )
    op.add_column(
        'auctions',
        sa.Column('preview_x_display_name', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'auctions',
        sa.Column('preview_x_avatar_url', sa.String(length=1024), nullable=True),
    )
    op.add_column(
        'auctions',
        sa.Column('preview_x_banner_url', sa.String(length=1024), nullable=True),
    )
    op.add_column(
        'auctions',
        sa.Column('preview_x_bio', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('auctions', 'preview_x_bio')
    op.drop_column('auctions', 'preview_x_banner_url')
    op.drop_column('auctions', 'preview_x_avatar_url')
    op.drop_column('auctions', 'preview_x_display_name')
    op.drop_column('auctions', 'preview_x_handle')
    op.drop_column('auctions', 'project_x_url')
