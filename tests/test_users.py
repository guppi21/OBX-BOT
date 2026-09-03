import pytest
from sqlalchemy.exc import IntegrityError
from packages.database.models.user import User
from packages.shared.exceptions import UserNotFoundError


def test_create_user_and_wallet_initialization(wallet_service):
    discord_id = "123456789012345678"
    user, wallet, created = wallet_service.get_or_create_user(discord_id)

    assert created is True
    assert user.discord_user_id == discord_id
    assert user.id is not None
    assert wallet.user_id == user.id
    assert wallet.available_balance == 0
    assert wallet.locked_balance == 0
    assert wallet.total_balance == 0


def test_get_or_create_user_existing(wallet_service):
    discord_id = "987654321098765432"
    user1, wallet1, created1 = wallet_service.get_or_create_user(discord_id)
    assert created1 is True

    user2, wallet2, created2 = wallet_service.get_or_create_user(discord_id)
    assert created2 is False
    assert user1.id == user2.id
    assert wallet1.id == wallet2.id


def test_duplicate_user_database_constraint(db_session):
    discord_id = "111222333444555666"
    user1 = User(discord_user_id=discord_id)
    db_session.add(user1)
    db_session.commit()

    user2 = User(discord_user_id=discord_id)
    db_session.add(user2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_not_found_raises_error(wallet_service):
    with pytest.raises(UserNotFoundError) as exc_info:
        wallet_service.get_user("non_existent_id")
    assert exc_info.value.code == "USER_NOT_FOUND"
