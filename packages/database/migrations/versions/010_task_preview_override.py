"""Task admin preview overrides

Revision ID: 010_task_preview_override
Revises: 009_task_url_preview
Create Date: 2026-09-03 00:58:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010_task_preview_override'
down_revision: Union[str, None] = '009_task_url_preview'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('preview_author_override', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_title_override', sa.String(length=512), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_text_override', sa.Text(), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_image_override', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'preview_image_override')
    op.drop_column('tasks', 'preview_text_override')
    op.drop_column('tasks', 'preview_title_override')
    op.drop_column('tasks', 'preview_author_override')
