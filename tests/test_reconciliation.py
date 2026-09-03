import pytest
from apps.obx_core.services.reconciliation import ReconciliationService
from packages.database.models.wallet import Wallet
from packages.database.models.ledger import LedgerEntry
from packages.shared.enums import TransactionType


def test_reconciliation_empty_system(db_session):
    recon = ReconciliationService(db_session)
    report = recon.reconcile_all()
    assert report.is_consistent is True
    assert report.total_users_checked == 0
    assert report.mismatched_users_count == 0


def test_reconciliation_consistent_user(wallet_service, db_session):
    user_id = "user_recon_clean"
    # Credit 1000
    wallet_service.credit(user_id, 1000, "bonus", "rec_k1")
    # Debit 200 (Avail: 800)
    wallet_service.debit(user_id, 200, "store", "rec_k2")
    # Lock 300 (Avail: 500, Locked: 300)
    wallet_service.lock_funds(user_id, 300, "bid", "rec_k3")
    # Release 100 (Avail: 600, Locked: 200)
    wallet_service.release_funds(user_id, 100, "outbid", "rec_k4")

    recon = ReconciliationService(db_session)
    disc = recon.reconcile_user(user_id)
    assert disc is None

    report = recon.reconcile_all()
    assert report.is_consistent is True
    assert report.total_users_checked == 1
    assert report.mismatched_users_count == 0


def test_reconciliation_detects_corrupted_available_balance(wallet_service, db_session):
    user_id = "user_recon_corrupt_avail"
    user, wallet, _ = wallet_service.get_or_create_user(user_id)
    wallet_service.credit(user_id, 500, "bonus", "corrupt_k1")

    # Manually tamper with wallet balance (simulate database corruption / rogue update)
    wallet.available_balance = 9999
    db_session.commit()

    recon = ReconciliationService(db_session)
    disc = recon.reconcile_user(user_id)

    assert disc is not None
    assert disc.discord_user_id == user_id
    assert disc.actual_available == 9999
    assert disc.expected_available == 500
    assert disc.available_diff == 9499
    assert disc.actual_locked == 0
    assert disc.expected_locked == 0

    report = recon.reconcile_all()
    assert report.is_consistent is False
    assert report.mismatched_users_count == 1


def test_reconciliation_detects_corrupted_locked_balance(wallet_service, db_session):
    user_id = "user_recon_corrupt_lock"
    user, wallet, _ = wallet_service.get_or_create_user(user_id)
    wallet_service.credit(user_id, 1000, "bonus", "corrupt_lock_k1")
    wallet_service.lock_funds(user_id, 300, "bid", "corrupt_lock_k2")

    # Tamper with locked balance
    wallet.locked_balance = 777
    db_session.commit()

    recon = ReconciliationService(db_session)
    disc = recon.reconcile_user(user_id)

    assert disc is not None
    assert disc.actual_locked == 777
    assert disc.expected_locked == 300
    assert disc.locked_diff == 477


def test_system_wide_reconciliation_multi_user(wallet_service, db_session):
    user1 = "multi_clean_1"
    user2 = "multi_clean_2"
    user3 = "multi_corrupt_3"

    wallet_service.credit(user1, 100, "bonus", "u1_k1")
    wallet_service.credit(user2, 200, "bonus", "u2_k1")
    _, w3, _ = wallet_service.get_or_create_user(user3)
    wallet_service.credit(user3, 300, "bonus", "u3_k1")

    # Corrupt user3
    w3.available_balance = 1000
    db_session.commit()

    recon = ReconciliationService(db_session)
    report = recon.reconcile_all()

    assert report.is_consistent is False
    assert report.total_users_checked == 3
    assert report.mismatched_users_count == 1
    assert report.discrepancies[0].discord_user_id == user3
