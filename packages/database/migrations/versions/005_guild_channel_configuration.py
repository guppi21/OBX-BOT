"""Guild channel routing configuration and published message tracking

Revision ID: 005_guild_channel_configuration
Revises: 004_whitelist_auctions
Create Date: 2026-09-02 04:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005_guild_channel_configuration'
down_revision: Union[str, None] = '004_whitelist_auctions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Guild Config table
    op.create_table(
        'guild_configs',
        sa.Column('guild_id', sa.String(length=32), nullable=False),
        sa.Column('tasks_channel_id', sa.String(length=32), nullable=True),
        sa.Column('leaderboard_channel_id', sa.String(length=32), nullable=True),
        sa.Column('auctions_channel_id', sa.String(length=32), nullable=True),
        sa.Column('winners_channel_id', sa.String(length=32), nullable=True),
        sa.Column('admin_channel_id', sa.String(length=32), nullable=True),
        sa.Column('economy_channel_id', sa.String(length=32), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('guild_id', name='pk_guild_configs'),
    )

    # 2. Published Messages table
    op.create_table(
        'published_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('guild_id', sa.String(length=32), nullable=False),
        sa.Column('feature_type', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=64), server_default='DEFAULT', nullable=False),
        sa.Column('channel_id', sa.String(length=32), nullable=False),
        sa.Column('message_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_published_messages'),
        sa.UniqueConstraint('guild_id', 'feature_type', 'source_id', name='uq_published_msg_guild_feature_src'),
    )
    op.create_index('ix_published_messages_guild_id', 'published_messages', ['guild_id'])
    op.create_index('ix_published_messages_feature_type', 'published_messages', ['feature_type'])
    op.create_index('ix_published_messages_source_id', 'published_messages', ['source_id'])


def downgrade() -> None:
    op.drop_index('ix_published_messages_source_id', table_name='published_messages')
    op.drop_index('ix_published_messages_feature_type', table_name='published_messages')
    op.drop_index('ix_published_messages_guild_id', table_name='published_messages')
    op.drop_table('published_messages')
    op.drop_table('guild_configs')
