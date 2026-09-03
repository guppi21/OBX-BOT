"""Task lifecycle, submission review audit logs and proof media cleanup

Revision ID: 007_task_lifecycle_proof
Revises: 006_task_alerts_role
Create Date: 2026-09-02 06:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '007_task_lifecycle_proof'
down_revision: Union[str, None] = '006_task_alerts_role'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add proof media cleanup columns to task_submissions
    op.add_column(
        'task_submissions',
        sa.Column('proof_media_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column(
        'task_submissions',
        sa.Column('proof_media_deleted_at', sa.DateTime(timezone=True), nullable=True),
    )

    # 2. Create submission_audit_logs table
    op.create_table(
        'submission_audit_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('submission_id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('admin_id', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('previous_status', sa.String(length=32), nullable=False),
        sa.Column('new_status', sa.String(length=32), nullable=False),
        sa.Column('reward_amount', sa.BigInteger(), nullable=True),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('obx_transaction_id', sa.Uuid(), nullable=True),
        sa.Column('proof_media_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('proof_media_deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['task_submissions.id'], ondelete='CASCADE', name='fk_sub_audit_sub_id'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE', name='fk_sub_audit_task_id'),
        sa.PrimaryKeyConstraint('id', name='pk_submission_audit_logs'),
    )
    op.create_index('ix_sub_audit_task_id', 'submission_audit_logs', ['task_id'])
    op.create_index('ix_sub_audit_submission_id', 'submission_audit_logs', ['submission_id'])
    op.create_index('ix_sub_audit_discord_user_id', 'submission_audit_logs', ['discord_user_id'])
    op.create_index('ix_sub_audit_admin_id', 'submission_audit_logs', ['admin_id'])
    op.create_index('ix_sub_audit_created_at', 'submission_audit_logs', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_sub_audit_created_at', table_name='submission_audit_logs')
    op.drop_index('ix_sub_audit_admin_id', table_name='submission_audit_logs')
    op.drop_index('ix_sub_audit_discord_user_id', table_name='submission_audit_logs')
    op.drop_index('ix_sub_audit_submission_id', table_name='submission_audit_logs')
    op.drop_index('ix_sub_audit_task_id', table_name='submission_audit_logs')
    op.drop_table('submission_audit_logs')
    op.drop_column('task_submissions', 'proof_media_deleted_at')
    op.drop_column('task_submissions', 'proof_media_deleted')
