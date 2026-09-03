"""Add task_alerts_role_id to guild_configs

Revision ID: 006_task_alerts_role
Revises: 005_guild_channel_configuration
Create Date: 2026-09-02 05:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '006_task_alerts_role'
down_revision: Union[str, None] = '005_guild_channel_configuration'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'guild_configs',
        sa.Column('task_alerts_role_id', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('guild_configs', 'task_alerts_role_id')
