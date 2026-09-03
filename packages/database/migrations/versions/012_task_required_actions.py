"""Add required_actions to tasks table

Revision ID: 012_task_required_actions
Revises: 011_task_preview_source_status
Create Date: 2026-09-03 01:31:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '012_task_required_actions'
down_revision: Union[str, None] = '011_task_preview_source_status'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('required_actions', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'required_actions')
