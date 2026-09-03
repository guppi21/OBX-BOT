import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from datetime import datetime, timezone, timedelta

from packages.shared.config import get_settings
from apps.obx_tasks.bot.permissions import has_raider_role, check_raider_access
from apps.obx_tasks.bot.join_raid_views import (
    build_join_raid_embed,
    JoinRaidView,
    handle_join_raid_click,
)
from apps.obx_tasks.bot.announcement_service import (
    announce_task,
    announce_auction,
    announce_auction_winners,
    announce_auction_ending_soon,
    deploy_or_update_join_raid_center,
)
from packages.shared.enums import TaskStatus, AuctionStatus, AuctionType
from tests.test_discord_channels import mock_session_scope_for


@pytest.fixture(autouse=True)
def configure_raid_settings(monkeypatch):
    """Ensure RAID_ROLE_ID and RAID_JOIN_CHANNEL_ID are configured for testing."""
    s = get_settings()
    monkeypatch.setattr(s, "RAID_ROLE_ID", "9988776655")
    monkeypatch.setattr(s, "RAID_JOIN_CHANNEL_ID", "1122334455")
    monkeypatch.setattr(s, "ENABLE_EVERYONE_ANNOUNCEMENTS", False)


def test_has_raider_role_with_configured_role_id():
    mock_member = MagicMock(spec=discord.Member)
    mock_member.guild_permissions = MagicMock(administrator=False)
    mock_role = MagicMock(spec=discord.Role, id=9988776655, name="SomeRole")
    mock_member.roles = [mock_role]

    assert has_raider_role(mock_member) is True


def test_has_raider_role_with_role_name():
    mock_member = MagicMock(spec=discord.Member)
    mock_member.guild_permissions = MagicMock(administrator=False)
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 12345
    mock_role.name = "⚡ OBX Raider"
    mock_member.roles = [mock_role]

    assert has_raider_role(mock_member) is True


def test_has_raider_role_admin_bypass():
    mock_member = MagicMock(spec=discord.Member)
    mock_member.guild_permissions = MagicMock(administrator=True)
    mock_member.roles = []

    assert has_raider_role(mock_member) is True


def test_has_raider_role_denies_normal_user_without_role():
    mock_member = MagicMock(spec=discord.Member)
    mock_member.guild_permissions = MagicMock(administrator=False, manage_guild=False)
    other_role = MagicMock(spec=discord.Role, id=55555, name="Guest")
    mock_member.roles = [other_role]

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = mock_member

    assert has_raider_role(mock_interaction) is False


@pytest.mark.asyncio
async def test_check_raider_access_prompts_unverified_member(db_session):
    mock_member = MagicMock(spec=discord.Member, id="unverified_1")
    mock_member.guild_permissions = MagicMock(administrator=False, manage_guild=False)
    mock_member.roles = []

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = mock_member
    mock_interaction.guild = MagicMock()
    mock_interaction.response = MagicMock()
    mock_interaction.response.is_done = MagicMock(return_value=False)
    mock_interaction.response.send_message = AsyncMock()

    with patch("apps.obx_tasks.bot.permissions.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        allowed = await check_raider_access(mock_interaction)
    assert allowed is False

    mock_interaction.response.send_message.assert_called_once()
    kwargs = mock_interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert "JOIN THE OBX RAID" in embed.title


@pytest.mark.asyncio
async def test_handle_join_raid_click_without_twitter_prompts_twitter(db_session):
    mock_guild = MagicMock(spec=discord.Guild, id="1542965409383321660")
    mock_raider_role = MagicMock(spec=discord.Role, id=9988776655, name="⚡ OBX Raider")
    mock_guild.get_role.return_value = mock_raider_role

    mock_member = MagicMock(spec=discord.Member, id="445566", display_name="CryptoChamber")
    mock_member.guild_permissions = MagicMock(administrator=False, manage_guild=False)
    mock_member.roles = []
    mock_member.add_roles = AsyncMock()

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = mock_guild
    mock_interaction.user = mock_member
    mock_interaction.response = MagicMock()
    mock_interaction.response.is_done = MagicMock(return_value=False)
    mock_interaction.response.send_message = AsyncMock()

    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_join_raid_click(mock_interaction)

    mock_member.add_roles.assert_not_called()
    mock_interaction.response.send_message.assert_called_once()
    kwargs = mock_interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert "JOIN THE OBX RAID" in embed.title
    labels = [b.label for b in kwargs.get("view").children if hasattr(b, "label")]
    assert "Set X Account" in labels or "SET YOUR TWITTER" in labels


@pytest.mark.asyncio
async def test_handle_join_raid_click_assigns_role_when_twitter_set(db_session):
    from apps.obx_tasks.services.raider_service import RaiderService
    r_service = RaiderService(db_session)
    r_service.set_raider_twitter("445566", "@CryptoChamber")

    mock_guild = MagicMock(spec=discord.Guild, id="1542965409383321660")
    mock_raider_role = MagicMock(spec=discord.Role, id=9988776655, name="⚡ OBX Raider")
    mock_guild.get_role.return_value = mock_raider_role

    mock_member = MagicMock(spec=discord.Member, id="445566", display_name="CryptoChamber")
    mock_member.guild_permissions = MagicMock(administrator=False, manage_guild=False)
    mock_member.roles = []
    mock_member.add_roles = AsyncMock()
    mock_guild.get_member.return_value = mock_member

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = mock_guild
    mock_interaction.user = mock_member
    mock_interaction.response = MagicMock()
    mock_interaction.response.is_done = MagicMock(return_value=False)
    mock_interaction.response.send_message = AsyncMock()

    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_join_raid_click(mock_interaction)

    mock_member.add_roles.assert_called_once_with(
        mock_raider_role,
        reason="User joined the OBX Raid via onboarding flow",
    )
    mock_interaction.response.send_message.assert_called_once()
    kwargs = mock_interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert "YOU'RE IN" in embed.title
    assert "@CryptoChamber" in embed.description


@pytest.mark.asyncio
async def test_handle_join_raid_click_already_member(db_session):
    from apps.obx_tasks.services.raider_service import RaiderService
    r_service = RaiderService(db_session)
    r_service.set_raider_twitter("445566", "@CryptoChamber")

    mock_guild = MagicMock(spec=discord.Guild, id="1542965409383321660")
    mock_raider_role = MagicMock(spec=discord.Role, id=9988776655, name="⚡ OBX Raider")
    mock_guild.get_role.return_value = mock_raider_role

    mock_member = MagicMock(spec=discord.Member, id="445566", display_name="CryptoChamber")
    mock_member.roles = [mock_raider_role]
    mock_member.add_roles = AsyncMock()

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = mock_guild
    mock_interaction.user = mock_member
    mock_interaction.response = MagicMock()
    mock_interaction.response.is_done = MagicMock(return_value=False)
    mock_interaction.response.send_message = AsyncMock()

    with patch("apps.obx_tasks.bot.join_raid_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await handle_join_raid_click(mock_interaction)

    mock_member.add_roles.assert_not_called()
    mock_interaction.response.send_message.assert_called_once()
    kwargs = mock_interaction.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert "OBX RAIDER ACTIVE" in embed.title
    assert "@CryptoChamber" in embed.description


@pytest.mark.asyncio
async def test_announcements_ping_raid_role_never_everyone(db_session):
    from apps.obx_tasks.services.channel_service import ChannelService
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.services.auction_service import AuctionService

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_raid_test", "tasks", "101", "admin")
    ch_service.update_guild_channel("guild_raid_test", "auctions", "102", "admin")
    ch_service.update_guild_channel("guild_raid_test", "winners", "103", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Raid Role Task",
        description="Verify role ping",
        task_type="LIKE",
        target_url="https://x.com/obx/status/123",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="guild_raid_test")
    mock_guild.me = MagicMock()
    mock_ch_tasks = MagicMock(spec=discord.TextChannel, id=101, name="tasks")
    mock_ch_tasks.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_tasks.send = AsyncMock(return_value=MagicMock(id=888111))

    mock_ch_aucs = MagicMock(spec=discord.TextChannel, id=102, name="auctions")
    mock_ch_aucs.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_aucs.send = AsyncMock(return_value=MagicMock(id=888222))

    mock_ch_wins = MagicMock(spec=discord.TextChannel, id=103, name="winners")
    mock_ch_wins.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_wins.send = AsyncMock(return_value=MagicMock(id=888333))

    def mock_get_channel(cid):
        if cid == 101: return mock_ch_tasks
        if cid == 102: return mock_ch_aucs
        if cid == 103: return mock_ch_wins
        return None
    mock_guild.get_channel.side_effect = mock_get_channel

    mock_bot = MagicMock(spec=discord.Client)

    # 1. Announce Task: verify pings <@&9988776655> and ZERO @everyone
    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, _ = await announce_task(task, mock_guild, mock_bot)
        assert ok is True

    task_send_kw = mock_ch_tasks.send.call_args_list[0][1]
    assert task_send_kw["content"] == "<@&9988776655>"
    assert "@everyone" not in task_send_kw["content"]

    # 2. Announce Auction: verify pings <@&9988776655> and ZERO @everyone
    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="Raid Auction",
        description="WL auction",
        reward_title="1x Pass",
        total_slots=1,
        price_or_min_bid=10,
        ends_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by="admin_1",
    )

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, _ = await announce_auction(auc, mock_guild, mock_bot)
        assert ok is True

    auc_send_kw = mock_ch_aucs.send.call_args[1]
    assert auc_send_kw["content"] == "<@&9988776655>"
    assert "@everyone" not in auc_send_kw["content"]

    # 3. Publish Winners: verify pings <@&9988776655> and ZERO @everyone
    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, _ = await announce_auction_winners(auc, [], 0, mock_guild, mock_bot)
        assert ok is True

    win_send_kw = mock_ch_wins.send.call_args[1]
    assert win_send_kw["content"] == "<@&9988776655>"
    assert "@everyone" not in win_send_kw["content"]

    # 4. Auction Ending Warning: verify pings <@&9988776655> and ZERO @everyone
    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, _ = await announce_auction_ending_soon(auc, mock_guild, mock_bot)
        assert ok is True

    warn_send_kw = mock_ch_aucs.send.call_args_list[-1][1]
    assert warn_send_kw["content"] == "<@&9988776655>"
    assert "@everyone" not in warn_send_kw["content"]


@pytest.mark.asyncio
async def test_deploy_join_raid_center(db_session):
    mock_guild = MagicMock(spec=discord.Guild, id="guild_join_test")
    mock_guild.me = MagicMock()
    mock_channel = MagicMock(spec=discord.TextChannel, id=1122334455, name="⚡・join-raid")
    mock_channel.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_channel.send = AsyncMock(return_value=MagicMock(id=99999))
    mock_guild.get_channel.return_value = mock_channel
    mock_guild.text_channels = [mock_channel]

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, msg = await deploy_or_update_join_raid_center(mock_guild, mock_bot)
        assert ok is True

    mock_channel.send.assert_called_once()
    kwargs = mock_channel.send.call_args[1]
    embed = kwargs.get("embed")
    view = kwargs.get("view")

    assert "⚡ JOIN THE OBX RAID" in embed.title
    assert isinstance(view, JoinRaidView)
    assert view.children[0].custom_id == "obx:join_raid"
    assert view.children[0].label in ("JOIN THE RAID", "Join the Raid")
