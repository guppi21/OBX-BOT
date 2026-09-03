"""Task management and notification configuration

Revision ID: 008_task_mgmt_notif
Revises: 007_task_lifecycle_proof
Create Date: 2026-09-02 23:57:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '008_task_mgmt_notif'
down_revision: Union[str, None] = '007_task_lifecycle_proof'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tasks',
        sa.Column('proof_required', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )
    op.add_column(
        'tasks',
        sa.Column('allow_image_proof', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )
    op.add_column(
        'tasks',
        sa.Column('notification_type', sa.String(length=16), server_default='DEFAULT', nullable=False),
    )
    op.add_column(
        'tasks',
        sa.Column('custom_notification_template', sa.Text(), nullable=True),
    )
    op.add_column(
        'tasks',
        sa.Column('cancellation_reason', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('tasks', 'cancellation_reason')
    op.drop_column('tasks', 'custom_notification_template')
    op.drop_column('tasks', 'notification_type')
    op.drop_column('tasks', 'allow_image_proof')
    op.drop_column('tasks', 'proof_required')
