import pytest
from packages.shared.exceptions import IdempotencyConflictError


def test_credit_idempotency_same_key_returns_same_entry(wallet_service):
    discord_id = "user_idem_credit"
    key = "idem_credit_001"

    entry1 = wallet_service.credit(
        discord_user_id=discord_id,
        amount=500,
        reference_type="bonus",
        idempotency_key=key,
    )

    entry2 = wallet_service.credit(
        discord_user_id=discord_id,
        amount=500,
        reference_type="bonus",
        idempotency_key=key,
    )

    assert entry1.id == entry2.id
    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 500  # Only credited once!


def test_debit_idempotency_same_key(wallet_service):
    discord_id = "user_idem_debit"
    wallet_service.credit(discord_id, 1000, "seed", "seed_idem_1")

    key = "idem_deb_001"
    entry1 = wallet_service.debit(discord_id, 300, "purchase", key)
    entry2 = wallet_service.debit(discord_id, 300, "purchase", key)

    assert entry1.id == entry2.id
    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 700  # Debited once


def test_lock_idempotency_same_key(wallet_service):
    discord_id = "user_idem_lock"
    wallet_service.credit(discord_id, 1000, "seed", "seed_idem_2")

    key = "idem_lock_001"
    entry1 = wallet_service.lock_funds(discord_id, 400, "bid", key)
    entry2 = wallet_service.lock_funds(discord_id, 400, "bid", key)

    assert entry1.id == entry2.id
    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 600
    assert bal["locked_balance"] == 400


def test_release_idempotency_same_key(wallet_service):
    discord_id = "user_idem_release"
    wallet_service.credit(discord_id, 1000, "seed", "seed_idem_3")
    wallet_service.lock_funds(discord_id, 500, "bid", "lock_idem_3")

    key = "idem_rel_001"
    entry1 = wallet_service.release_funds(discord_id, 200, "outbid", key)
    entry2 = wallet_service.release_funds(discord_id, 200, "outbid", key)

    assert entry1.id == entry2.id
    bal = wallet_service.get_balance(discord_id)
    assert bal["available_balance"] == 700
    assert bal["locked_balance"] == 300


def test_idempotency_conflict_different_amount(wallet_service):
    discord_id = "user_idem_conflict_amt"
    key = "conflict_key_1"

    wallet_service.credit(discord_id, 500, "bonus", key)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        wallet_service.credit(discord_id, 600, "bonus", key)

    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
    assert exc_info.value.idempotency_key == key


def test_idempotency_conflict_different_user(wallet_service):
    user_a = "user_conflict_a"
    user_b = "user_conflict_b"
    key = "conflict_key_2"

    wallet_service.credit(user_a, 500, "bonus", key)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        wallet_service.credit(user_b, 500, "bonus", key)

    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"


def test_idempotency_conflict_different_type(wallet_service):
    discord_id = "user_idem_conflict_type"
    wallet_service.credit(discord_id, 1000, "seed", "seed_conflict_type")
    key = "conflict_key_3"

    wallet_service.credit(discord_id, 200, "bonus", key)

    with pytest.raises(IdempotencyConflictError) as exc_info:
        wallet_service.debit(discord_id, 200, "purchase", key)

    assert exc_info.value.code == "IDEMPOTENCY_CONFLICT"
