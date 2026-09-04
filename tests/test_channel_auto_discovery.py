import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from apps.obx_tasks.services.channel_service import (
    ChannelService,
    score_channel_match,
)
from packages.database.models.channel_config import GuildConfig


def test_score_channel_match():
    # Exact match
    assert score_channel_match("tasks", ["tasks"]) == 100
    assert score_channel_match("🏆-winners", ["winners"]) == 100
    assert score_channel_match("🔒│admin-logs", ["admin-logs"]) == 100

    # Token match
    assert score_channel_match("obx-tasks", ["tasks"]) >= 80
    assert score_channel_match("daily-bounties", ["bounties"]) >= 80

    # Prefix / suffix match
    assert score_channel_match("tasks-chat", ["tasks"]) >= 50
    assert score_channel_match("server-admin", ["admin"]) >= 50

    # No match
    assert score_channel_match("general", ["tasks", "auctions", "winners"]) == 0
    assert score_channel_match("announcements", ["tasks", "auctions"]) == 0


def test_auto_discover_guild_channels_standard_names(db_session):
    ch_service = ChannelService(db_session)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_auto_1"
    mock_guild.name = "Test Community Server"

    def create_mock_channel(cid: int, name: str):
        c = MagicMock(spec=discord.TextChannel)
        c.id = cid
        c.name = name
        return c

    ch_tasks = create_mock_channel(1001, "tasks")
    ch_auc = create_mock_channel(1002, "auctions")
    ch_win = create_mock_channel(1003, "winners")
    ch_lb = create_mock_channel(1004, "leaderboard")
    ch_admin = create_mock_channel(1005, "admin-logs")
    ch_gen = create_mock_channel(1006, "general")

    mock_guild.text_channels = [ch_gen, ch_tasks, ch_auc, ch_win, ch_lb, ch_admin]
    mock_guild.get_channel = lambda cid: next((c for c in mock_guild.text_channels if c.id == cid), None)

    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 1539356123553996913
    mock_role.name = "raid"
    mock_guild.get_role = lambda rid: mock_role if rid == 1539356123553996913 else None
    mock_guild.roles = [mock_role]

    discovered = ch_service.auto_discover_guild_channels(mock_guild)

    assert discovered["tasks"] == "tasks"
    assert discovered["auctions"] == "auctions"
    assert discovered["winners"] == "winners"
    assert discovered["leaderboard"] == "leaderboard"
    assert discovered["admin"] == "admin-logs"
    assert discovered["role"] == "raid"

    # Verify persisted in database
    config = db_session.query(GuildConfig).filter_by(guild_id="guild_auto_1").first()
    assert config is not None
    assert config.tasks_channel_id == "1001"
    assert config.auctions_channel_id == "1002"
    assert config.winners_channel_id == "1003"
    assert config.leaderboard_channel_id == "1004"
    assert config.admin_channel_id == "1005"
    assert config.task_alerts_role_id == "1539356123553996913"


def test_auto_discover_guild_channels_with_emojis_and_variations(db_session):
    ch_service = ChannelService(db_session)

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_auto_emojis"
    mock_guild.name = "Emoji Guild"

    def create_mock_channel(cid: int, name: str):
        c = MagicMock(spec=discord.TextChannel)
        c.id = cid
        c.name = name
        return c

    ch_tasks = create_mock_channel(2001, "⚔️-raid-tasks")
    ch_auc = create_mock_channel(2002, "🔨│wl-auctions")
    ch_win = create_mock_channel(2003, "🏆-auction-results")
    ch_lb = create_mock_channel(2004, "📊-rankings")
    ch_admin = create_mock_channel(2005, "🔒-obx-admin")

    mock_guild.text_channels = [ch_tasks, ch_auc, ch_win, ch_lb, ch_admin]
    mock_guild.get_channel = lambda cid: next((c for c in mock_guild.text_channels if c.id == cid), None)

    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 998877
    mock_role.name = "⚡ OBX Raider"
    mock_guild.get_role = lambda rid: None
    mock_guild.roles = [mock_role]

    discovered = ch_service.auto_discover_guild_channels(mock_guild)

    assert discovered["tasks"] == "⚔️-raid-tasks"
    assert discovered["auctions"] == "🔨│wl-auctions"
    assert discovered["winners"] == "🏆-auction-results"
    assert discovered["leaderboard"] == "📊-rankings"
    assert discovered["admin"] == "🔒-obx-admin"
    assert discovered["role"] == "⚡ OBX Raider"

    config = db_session.query(GuildConfig).filter_by(guild_id="guild_auto_emojis").first()
    assert config.tasks_channel_id == "2001"
    assert config.auctions_channel_id == "2002"
    assert config.winners_channel_id == "2003"
    assert config.leaderboard_channel_id == "2004"
    assert config.admin_channel_id == "2005"
    assert config.task_alerts_role_id == "998877"


def test_auto_discover_respects_manual_configuration(db_session):
    ch_service = ChannelService(db_session)

    # Manually configure tasks channel to custom ID 5555
    ch_service.update_guild_channel("guild_custom", "tasks", "5555", "admin_user")

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_custom"
    mock_guild.name = "Custom Guild"

    def create_mock_channel(cid: int, name: str):
        c = MagicMock(spec=discord.TextChannel)
        c.id = cid
        c.name = name
        return c

    ch_custom_tasks = create_mock_channel(5555, "custom-missions")
    ch_auto_tasks = create_mock_channel(6666, "tasks")
    ch_auc = create_mock_channel(7777, "auctions")

    mock_guild.text_channels = [ch_custom_tasks, ch_auto_tasks, ch_auc]
    mock_guild.get_channel = lambda cid: next((c for c in mock_guild.text_channels if c.id == cid), None)
    mock_guild.roles = []

    # Non-overwrite discovery
    discovered = ch_service.auto_discover_guild_channels(mock_guild, overwrite=False)

    # Tasks should NOT have been overwritten
    config = db_session.query(GuildConfig).filter_by(guild_id="guild_custom").first()
    assert config.tasks_channel_id == "5555"
    assert "tasks" not in discovered  # Already configured and valid
    assert discovered["auctions"] == "auctions"
    assert config.auctions_channel_id == "7777"

    # Now force overwrite=True
    discovered_overwrite = ch_service.auto_discover_guild_channels(mock_guild, overwrite=True)
    assert discovered_overwrite["tasks"] == "tasks"
    config_after = db_session.query(GuildConfig).filter_by(guild_id="guild_custom").first()
    assert config_after.tasks_channel_id == "6666"


@pytest.mark.asyncio
async def test_send_admin_log_event_auto_resolves_channel(db_session):
    from apps.obx_tasks.bot.announcement_service import send_admin_log_event

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_log_test"
    mock_guild.name = "Log Guild"

    ch_admin = MagicMock(spec=discord.TextChannel)
    ch_admin.id = 8888
    ch_admin.name = "admin-logs"
    ch_admin.send = AsyncMock()

    mock_guild.text_channels = [ch_admin]
    mock_guild.get_channel = lambda cid: ch_admin if cid == 8888 else None

    with patch("apps.obx_tasks.bot.announcement_service.session_scope") as mock_scope:
        from contextlib import contextmanager
        @contextmanager
        def _scope():
            yield db_session
        mock_scope.side_effect = _scope

        await send_admin_log_event(
            guild=mock_guild,
            title="Operational Test",
            description="Testing channel auto discovery",
            color=0x00FF00,
        )

    # Admin channel should have received send
    assert ch_admin.send.call_count == 1

    # Guild config should now have admin_channel_id auto-saved
    config = db_session.query(GuildConfig).filter_by(guild_id="guild_log_test").first()
    assert config is not None
    assert config.admin_channel_id == "8888"


@pytest.mark.asyncio
async def test_on_guild_join_auto_discovers_and_initializes(db_session):
    from apps.obx_tasks.bot.client import OBXTaskBot

    bot = MagicMock(spec=OBXTaskBot)
    bot.user = MagicMock(id=123456)
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_join_auto"
    mock_guild.name = "New Guild"

    mock_me = MagicMock(nick="OldBotName")
    mock_me.edit = AsyncMock()
    mock_guild.me = mock_me

    def create_mock_channel(cid: int, name: str):
        c = MagicMock(spec=discord.TextChannel)
        c.id = cid
        c.name = name
        return c

    ch_tasks = create_mock_channel(3001, "tasks")
    ch_auc = create_mock_channel(3002, "auctions")
    mock_guild.text_channels = [ch_tasks, ch_auc]
    mock_guild.get_channel = lambda cid: ch_tasks if cid == 3001 else (ch_auc if cid == 3002 else None)
    mock_guild.roles = []

    with patch("apps.obx_tasks.bot.client.session_scope") as mock_scope, \
         patch("apps.obx_tasks.bot.client.refresh_all_public_systems", new_callable=AsyncMock) as mock_refresh:
        from contextlib import contextmanager
        @contextmanager
        def _scope():
            yield db_session
        mock_scope.side_effect = _scope

        await OBXTaskBot.on_guild_join(bot, mock_guild)

    # Nickname auto-set to OBX
    mock_me.edit.assert_awaited_once_with(nick="OBX")

    # Slash commands synced
    bot.tree.copy_global_to.assert_called_with(guild=mock_guild)
    bot.tree.sync.assert_awaited_with(guild=mock_guild)

    # GuildConfig is initialized cleanly without auto-detect keyword sniffing
    config = db_session.query(GuildConfig).filter_by(guild_id="guild_join_auto").first()
    assert config is not None
    assert config.tasks_channel_id is None
    assert config.auctions_channel_id is None

    # Systems refreshed
    mock_refresh.assert_awaited_once_with(mock_guild, bot)
