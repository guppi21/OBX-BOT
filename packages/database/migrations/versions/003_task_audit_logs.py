"""Task audit logs

Revision ID: 003_task_audit_logs
Revises: 002_social_tasks
Create Date: 2026-09-02 02:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '003_task_audit_logs'
down_revision: Union[str, None] = '002_social_tasks'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table exists (for idempotency)
    op.create_table(
        'task_audit_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('changed_by', sa.String(length=64), nullable=False),
        sa.Column('field_name', sa.String(length=64), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_audit_logs_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_audit_logs')),
    )
    op.create_index(op.f('ix_task_audit_logs_task_id'), 'task_audit_logs', ['task_id'], unique=False)
    op.create_index(op.f('ix_task_audit_logs_changed_at'), 'task_audit_logs', ['changed_at'], unique=False)


def downgrade() -> None:
    op.drop_table('task_audit_logs')
