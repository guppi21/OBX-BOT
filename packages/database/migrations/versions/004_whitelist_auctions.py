"""Whitelist auctions and FCFS rewards

Revision ID: 004_whitelist_auctions
Revises: 003_task_audit_logs
Create Date: 2026-09-02 03:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '004_whitelist_auctions'
down_revision: Union[str, None] = '003_task_audit_logs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Auctions table
    op.create_table(
        'auctions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('reward_title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('auction_type', sa.String(length=32), nullable=False),
        sa.Column('total_slots', sa.Integer(), nullable=False),
        sa.Column('allocated_slots', sa.Integer(), server_default='0', nullable=False),
        sa.Column('price_or_min_bid', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='ACTIVE', nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('external_url', sa.String(length=1024), nullable=True),
        sa.Column('image_url', sa.String(length=1024), nullable=True),
        sa.Column('created_by', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('total_slots > 0', name='total_slots_positive'),
        sa.CheckConstraint('allocated_slots >= 0', name='allocated_slots_non_negative'),
        sa.CheckConstraint('allocated_slots <= total_slots', name='allocated_lte_total_slots'),
        sa.CheckConstraint('price_or_min_bid > 0', name='price_or_min_bid_positive'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_auctions')),
    )
    op.create_index(op.f('ix_auctions_auction_type'), 'auctions', ['auction_type'], unique=False)
    op.create_index(op.f('ix_auctions_status'), 'auctions', ['status'], unique=False)

    # 2. Auction Bids table
    op.create_table(
        'auction_bids',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('auction_id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('bid_amount', sa.BigInteger(), nullable=False),
        sa.Column('is_winner', sa.Boolean(), nullable=True),
        sa.Column('is_settled', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('placed_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('bid_amount > 0', name='bid_amount_positive'),
        sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_auction_bids_auction_id_auctions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_auction_bids')),
        sa.UniqueConstraint('auction_id', 'discord_user_id', name='uq_auction_bids_user'),
    )
    op.create_index(op.f('ix_auction_bids_auction_id'), 'auction_bids', ['auction_id'], unique=False)
    op.create_index(op.f('ix_auction_bids_discord_user_id'), 'auction_bids', ['discord_user_id'], unique=False)

    # 3. Auction Claims table (for FCFS)
    op.create_table(
        'auction_claims',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('auction_id', sa.Uuid(), nullable=False),
        sa.Column('discord_user_id', sa.String(length=64), nullable=False),
        sa.Column('price_paid', sa.BigInteger(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('obx_transaction_id', sa.Uuid(), nullable=False),
        sa.CheckConstraint('price_paid > 0', name='price_paid_positive'),
        sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_auction_claims_auction_id_auctions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_auction_claims')),
        sa.UniqueConstraint('auction_id', 'discord_user_id', name='uq_auction_claims_user'),
    )
    op.create_index(op.f('ix_auction_claims_auction_id'), 'auction_claims', ['auction_id'], unique=False)
    op.create_index(op.f('ix_auction_claims_discord_user_id'), 'auction_claims', ['discord_user_id'], unique=False)

    # 4. Auction Audit Logs table
    op.create_table(
        'auction_audit_logs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('auction_id', sa.Uuid(), nullable=False),
        sa.Column('changed_by', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('old_value', sa.Text(), nullable=True),
        sa.Column('new_value', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['auction_id'], ['auctions.id'], name=op.f('fk_auction_audit_logs_auction_id_auctions'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_auction_audit_logs')),
    )
    op.create_index(op.f('ix_auction_audit_logs_auction_id'), 'auction_audit_logs', ['auction_id'], unique=False)
    op.create_index(op.f('ix_auction_audit_logs_created_at'), 'auction_audit_logs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('auction_audit_logs')
    op.drop_table('auction_claims')
    op.drop_table('auction_bids')
    op.drop_table('auctions')
