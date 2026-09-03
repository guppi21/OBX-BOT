"""Add preview_image_url to auctions table

Revision ID: 014_auction_preview_image_url
Revises: 013_auction_project_x_url
Create Date: 2026-09-03 06:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '014_auction_preview_image_url'
down_revision: Union[str, None] = '013_auction_project_x_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'auctions',
        sa.Column('preview_image_url', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('auctions', 'preview_image_url')
