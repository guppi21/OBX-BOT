"""Task URL preview metadata persistence

Revision ID: 009_task_url_preview
Revises: 008_task_mgmt_notif
Create Date: 2026-09-03 00:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '009_task_url_preview'
down_revision: Union[str, None] = '008_task_mgmt_notif'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('preview_platform', sa.String(length=32), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_author', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_title', sa.String(length=512), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_description', sa.Text(), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_image_url', sa.String(length=1024), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_fetched_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'preview_fetched_at')
    op.drop_column('tasks', 'preview_image_url')
    op.drop_column('tasks', 'preview_description')
    op.drop_column('tasks', 'preview_title')
    op.drop_column('tasks', 'preview_author')
    op.drop_column('tasks', 'preview_platform')
