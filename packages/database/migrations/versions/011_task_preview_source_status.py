"""Add preview_source and preview_status to tasks

Revision ID: 011_task_preview_source_status
Revises: 010_task_preview_override
Create Date: 2026-09-03 01:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '011_task_preview_source_status'
down_revision: Union[str, None] = '010_task_preview_override'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('preview_source', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('preview_status', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'preview_status')
    op.drop_column('tasks', 'preview_source')
