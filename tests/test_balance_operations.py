import pytest
from packages.shared.enums import TransactionType
from packages.shared.exceptions import (
    InsufficientFundsError,
    InvalidAmountError,
)


def test_credit_funds_success(wallet_service):
    discord_id = "user_credit_test"
    entry = wallet_service.credit(
        discord_user_id=discord_id,
        amount=500,
        reference_type="signup_bonus",
        idempotency_key="cred_001",
        description="Initial bonus",
    )

    assert entry.amount == 500
    assert entry.transaction_type == TransactionType.CREDIT
    assert entry.reference_type == "signup_bonus"
    assert entry.idempotency_key == "cred_001"

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 500
    assert bal["locked_balance"] == 0
    assert bal["total_balance"] == 500


def test_debit_funds_success(wallet_service):
    discord_id = "user_debit_test"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=1000,
        reference_type="seed",
        idempotency_key="seed_001",
    )

    entry = wallet_service.debit(
        discord_user_id=discord_id,
        amount=400,
        reference_type="item_purchase",
        idempotency_key="deb_001",
        description="Purchased item #42",
    )

    assert entry.amount == 400
    assert entry.transaction_type == TransactionType.DEBIT

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 600
    assert bal["locked_balance"] == 0
    assert bal["total_balance"] == 600


def test_debit_funds_insufficient_balance(wallet_service):
    discord_id = "user_insufficient_debit"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=100,
        reference_type="seed",
        idempotency_key="seed_002",
    )

    with pytest.raises(InsufficientFundsError) as exc_info:
        wallet_service.debit(
            discord_user_id=discord_id,
            amount=150,
            reference_type="purchase",
            idempotency_key="deb_002",
        )

    assert exc_info.value.required == 150
    assert exc_info.value.available == 100
    assert exc_info.value.fund_type == "available"

    # Verify balance was unaffected
    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 100


def test_lock_funds_success(wallet_service):
    discord_id = "user_lock_test"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=1000,
        reference_type="seed",
        idempotency_key="seed_003",
    )

    entry = wallet_service.lock_funds(
        discord_user_id=discord_id,
        amount=300,
        reference_type="auction_bid",
        idempotency_key="lock_001",
        description="Bid on Auction #1",
    )

    assert entry.amount == 300
    assert entry.transaction_type == TransactionType.LOCK

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 700
    assert bal["locked_balance"] == 300
    assert bal["total_balance"] == 1000


def test_lock_funds_insufficient_available_balance(wallet_service):
    discord_id = "user_lock_insufficient"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=200,
        reference_type="seed",
        idempotency_key="seed_004",
    )

    with pytest.raises(InsufficientFundsError) as exc_info:
        wallet_service.lock_funds(
            discord_user_id=discord_id,
            amount=500,
            reference_type="auction_bid",
            idempotency_key="lock_002",
        )

    assert exc_info.value.required == 500
    assert exc_info.value.available == 200

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 200
    assert bal["locked_balance"] == 0


def test_release_funds_success(wallet_service):
    discord_id = "user_release_test"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=1000,
        reference_type="seed",
        idempotency_key="seed_005",
    )
    wallet_service.lock_funds(
        discord_user_id=discord_id,
        amount=400,
        reference_type="auction_bid",
        idempotency_key="lock_003",
    )

    entry = wallet_service.release_funds(
        discord_user_id=discord_id,
        amount=250,
        reference_type="auction_outbid",
        idempotency_key="rel_001",
        description="Outbid on Auction #1",
    )

    assert entry.amount == 250
    assert entry.transaction_type == TransactionType.RELEASE

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 850
    assert bal["locked_balance"] == 150
    assert bal["total_balance"] == 1000


def test_release_funds_insufficient_locked_balance(wallet_service):
    discord_id = "user_release_insufficient"
    wallet_service.credit(
        discord_user_id=discord_id,
        amount=1000,
        reference_type="seed",
        idempotency_key="seed_006",
    )
    wallet_service.lock_funds(
        discord_user_id=discord_id,
        amount=100,
        reference_type="auction_bid",
        idempotency_key="lock_004",
    )

    with pytest.raises(InsufficientFundsError) as exc_info:
        wallet_service.release_funds(
            discord_user_id=discord_id,
            amount=200,
            reference_type="auction_outbid",
            idempotency_key="rel_002",
        )

    assert exc_info.value.required == 200
    assert exc_info.value.available == 100
    assert exc_info.value.fund_type == "locked"

    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 900
    assert bal["locked_balance"] == 100


def test_invalid_amount_zero_or_negative(wallet_service):
    discord_id = "user_invalid_amount"
    wallet_service.get_or_create_user(discord_id)

    with pytest.raises(InvalidAmountError):
        wallet_service.credit(discord_id, 0, "test", "key_zero")

    with pytest.raises(InvalidAmountError):
        wallet_service.credit(discord_id, -100, "test", "key_neg")

    with pytest.raises(InvalidAmountError):
        wallet_service.debit(discord_id, -50, "test", "key_neg_deb")

    with pytest.raises(InvalidAmountError):
        wallet_service.lock_funds(discord_id, 0, "test", "key_lock_zero")
