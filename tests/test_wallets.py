import pytest
import uuid
from sqlalchemy.exc import IntegrityError
from packages.database.models.user import User
from packages.database.models.wallet import Wallet


def test_wallet_initial_state(wallet_service):
    user, wallet, _ = wallet_service.get_or_create_user("user_initial_test")
    assert wallet.available_balance == 0
    assert wallet.locked_balance == 0
    assert wallet.total_balance == 0
    assert wallet.user_id == user.id


def test_wallet_unique_user_id_constraint(db_session):
    user = User(discord_user_id="user_duplicate_wallet")
    db_session.add(user)
    db_session.commit()

    wallet1 = Wallet(user_id=user.id, available_balance=100, locked_balance=0)
    db_session.add(wallet1)
    db_session.commit()

    wallet2 = Wallet(user_id=user.id, available_balance=50, locked_balance=0)
    db_session.add(wallet2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_wallet_non_negative_available_balance_check_constraint(db_session):
    user = User(discord_user_id="user_negative_avail")
    db_session.add(user)
    db_session.commit()

    wallet = Wallet(user_id=user.id, available_balance=-50, locked_balance=0)
    db_session.add(wallet)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_wallet_non_negative_locked_balance_check_constraint(db_session):
    user = User(discord_user_id="user_negative_locked")
    db_session.add(user)
    db_session.commit()

    wallet = Wallet(user_id=user.id, available_balance=0, locked_balance=-10)
    db_session.add(wallet)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_wallet_total_balance_property(db_session):
    user = User(discord_user_id="user_total_bal")
    db_session.add(user)
    db_session.commit()

    wallet = Wallet(user_id=user.id, available_balance=750, locked_balance=250)
    db_session.add(wallet)
    db_session.commit()

    assert wallet.total_balance == 1000
