"""Social tasks foundation

Revision ID: 002_social_tasks
Revises: 001_initial_core
Create Date: 2026-09-02 01:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '002_social_tasks'
down_revision: Union[str, None] = '001_initial_core'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tasks table
    op.create_table(
        'tasks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('platform', sa.String(length=32), server_default='X', nullable=False),
        sa.Column('task_type', sa.String(length=32), nullable=False),
        sa.Column('target_url', sa.String(length=1024), nullable=False),
        sa.Column('reward_per_user', sa.BigInteger(), nullable=False),
        sa.Column('total_reward_pool', sa.BigInteger(), nullable=False),
        sa.Column('distributed_reward', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=32), server_default='DRAFT', nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('reward_per_user > 0', name='reward_per_user_positive'),
        sa.CheckConstraint('total_reward_pool >= reward_per_user', name='total_reward_pool_gte_reward_per_user'),
        sa.CheckConstraint('distributed_reward >= 0', name='distributed_reward_non_negative'),
        sa.CheckConstraint('distributed_reward <= total_reward_pool', name='distributed_lte_total_reward_pool'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks')),
    )
    op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)
    op.create_index(op.f('ix_tasks_task_type'), 'tasks', ['task_type'], unique=False)

    # Task Submissions table
    op.create_table(
        'task_submissions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('x_username', sa.String(length=64), nullable=False),
        sa.Column('proof_url', sa.String(length=1024), nullable=False),
        sa.Column('proof_text', sa.Text(), nullable=False),
        sa.Column('proof_screenshot_url', sa.String(length=1024), nullable=True),
        sa.Column('status', sa.String(length=32), server_default='PENDING', nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('reviewed_by', sa.String(length=64), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.String(length=500), nullable=True),
        sa.Column('reward_amount', sa.BigInteger(), nullable=True),
        sa.Column('obx_transaction_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], name=op.f('fk_task_submissions_task_id_tasks'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_submissions')),
        sa.UniqueConstraint('task_id', 'discord_user_id', name='uq_submissions_task_user'),
    )
    op.create_index(op.f('ix_task_submissions_task_id'), 'task_submissions', ['task_id'], unique=False)
    op.create_index(op.f('ix_task_submissions_discord_user_id'), 'task_submissions', ['discord_user_id'], unique=False)
    op.create_index(op.f('ix_task_submissions_status'), 'task_submissions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('task_submissions')
    op.drop_table('tasks')
