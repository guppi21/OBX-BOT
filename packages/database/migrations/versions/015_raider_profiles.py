"""Add raider_profiles table

Revision ID: 015_raider_profiles
Revises: 014_auction_preview_image_url
Create Date: 2026-09-03 06:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '015_raider_profiles'
down_revision: Union[str, None] = '014_auction_preview_image_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'raider_profiles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('twitter_handle', sa.String(length=64), nullable=False),
        sa.Column('twitter_profile_url', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_raider_profiles')),
        sa.UniqueConstraint('discord_user_id', name=op.f('uq_raider_profiles_discord_user_id')),
        sa.UniqueConstraint('twitter_handle', name=op.f('uq_raider_profiles_twitter_handle')),
    )
    op.create_index(op.f('ix_raider_profiles_discord_user_id'), 'raider_profiles', ['discord_user_id'], unique=True)
    op.create_index(op.f('ix_raider_profiles_twitter_handle'), 'raider_profiles', ['twitter_handle'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_raider_profiles_twitter_handle'), table_name='raider_profiles')
    op.drop_index(op.f('ix_raider_profiles_discord_user_id'), table_name='raider_profiles')
    op.drop_table('raider_profiles')
