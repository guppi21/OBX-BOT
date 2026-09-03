"""Initial core schema

Revision ID: 001_initial_core
Revises: 
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '001_initial_core'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Users table
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
        sa.UniqueConstraint('discord_user_id', name=op.f('uq_users_discord_user_id')),
    )

    # Wallets table
    op.create_table(
        'wallets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('available_balance', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('locked_balance', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('available_balance >= 0', name='available_balance_non_negative'),
        sa.CheckConstraint('locked_balance >= 0', name='locked_balance_non_negative'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_wallets_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wallets')),
        sa.UniqueConstraint('user_id', name=op.f('uq_wallets_user_id')),
    )

    # Ledger entries table
    op.create_table(
        'ledger_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('transaction_type', sa.String(length=32), nullable=False),
        sa.Column('reference_type', sa.String(length=64), nullable=False),
        sa.Column('reference_id', sa.String(length=255), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('amount > 0', name='amount_positive'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ledger_entries_user_id_users'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_ledger_entries')),
        sa.UniqueConstraint('idempotency_key', name=op.f('uq_ledger_entries_idempotency_key')),
    )
    op.create_index(op.f('ix_ledger_entries_user_id'), 'ledger_entries', ['user_id'], unique=False)
    op.create_index(op.f('ix_ledger_entries_transaction_type'), 'ledger_entries', ['transaction_type'], unique=False)
    op.create_index(op.f('ix_ledger_entries_reference_type'), 'ledger_entries', ['reference_type'], unique=False)
    op.create_index(op.f('ix_ledger_entries_reference_id'), 'ledger_entries', ['reference_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ledger_entries')
    op.drop_table('wallets')
    op.drop_table('users')
