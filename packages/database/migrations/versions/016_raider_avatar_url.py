"""Add twitter_avatar_url to raider_profiles

Revision ID: 016_raider_avatar_url
Revises: 015_raider_profiles
Create Date: 2026-09-03 07:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '016_raider_avatar_url'
down_revision: Union[str, None] = '015_raider_profiles'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'raider_profiles',
        sa.Column('twitter_avatar_url', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('raider_profiles', 'twitter_avatar_url')
