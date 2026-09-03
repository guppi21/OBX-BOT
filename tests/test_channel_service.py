import pytest
from apps.obx_tasks.services.channel_service import ChannelService, ChannelConfigError


def test_get_or_create_guild_config_and_defaults(db_session):
    service = ChannelService(db_session)
    config = service.get_or_create_guild_config("123456789012345678")

    assert config.guild_id == "123456789012345678"
    assert config.tasks_channel_id is None
    assert config.leaderboard_channel_id is None
    assert config.auctions_channel_id is None
    assert config.winners_channel_id is None
    assert config.admin_channel_id is None


def test_update_guild_channel_mappings(db_session):
    service = ChannelService(db_session)
    guild_id = "999888777666555444"

    # Set tasks channel
    c1 = service.update_guild_channel(guild_id, "tasks", "111111111111111111", updated_by="admin_1")
    assert c1.tasks_channel_id == "111111111111111111"
    assert c1.updated_by == "admin_1"

    # Set leaderboard channel
    c2 = service.update_guild_channel(guild_id, "leaderboard", "222222222222222222", updated_by="admin_2")
    assert c2.leaderboard_channel_id == "222222222222222222"
    assert c2.tasks_channel_id == "111111111111111111"

    # Set auctions & winners
    service.update_guild_channel(guild_id, "auctions", "333333333333333333", updated_by="admin_1")
    service.update_guild_channel(guild_id, "winners", "444444444444444444", updated_by="admin_1")

    refreshed = service.get_or_create_guild_config(guild_id)
    assert refreshed.auctions_channel_id == "333333333333333333"
    assert refreshed.winners_channel_id == "444444444444444444"


def test_invalid_channel_key_raises_error(db_session):
    service = ChannelService(db_session)
    with pytest.raises(ChannelConfigError, match="Invalid channel key"):
        service.update_guild_channel("123", "invalid_key", "111", updated_by="admin_1")


def test_published_message_record_and_idempotency(db_session):
    service = ChannelService(db_session)
    guild_id = "guild_pub_1"

    # 1. Record first message
    rec1 = service.record_published_message(
        guild_id=guild_id,
        feature_type="TASK_DASHBOARD",
        channel_id="ch_1",
        message_id="msg_100",
    )
    assert rec1.channel_id == "ch_1"
    assert rec1.message_id == "msg_100"

    # 2. Update existing message record (e.g. refreshed dashboard)
    rec2 = service.record_published_message(
        guild_id=guild_id,
        feature_type="TASK_DASHBOARD",
        channel_id="ch_1",
        message_id="msg_200",
    )
    assert rec2.id == rec1.id
    assert rec2.message_id == "msg_200"

    # 3. Retrieve
    fetched = service.get_published_message(guild_id, "TASK_DASHBOARD")
    assert fetched is not None
    assert fetched.message_id == "msg_200"


def test_delete_published_message(db_session):
    service = ChannelService(db_session)
    guild_id = "guild_pub_del"

    service.record_published_message(guild_id, "LEADERBOARD", "ch_2", "msg_300")
    assert service.get_published_message(guild_id, "LEADERBOARD") is not None

    deleted = service.delete_published_message(guild_id, "LEADERBOARD")
    assert deleted is True
    assert service.get_published_message(guild_id, "LEADERBOARD") is None
