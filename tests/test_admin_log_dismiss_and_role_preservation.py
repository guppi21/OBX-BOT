import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.config import get_settings
from packages.shared.enums import TaskStatus, AuctionType
from packages.database.models.task import Task
from packages.database.models.auction import Auction
from apps.obx_tasks.bot.announcement_service import (
    AdminLogDismissView,
    send_admin_log_event,
    announce_auction,
    announce_task,
)
from apps.obx_tasks.services.channel_service import ChannelService
from tests.test_discord_channels import mock_session_scope_for


@pytest.mark.asyncio
async def test_admin_log_dismiss_view_admin_success():
    view = AdminLogDismissView()
    button = view.children[0]
    assert button.custom_id == "obx:admin:dismiss_log"
    assert button.label == "Dismiss"

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response.is_done.return_value = False
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.message = MagicMock()
    mock_interaction.message.delete = AsyncMock()

    with patch("apps.obx_tasks.bot.permissions.is_admin", return_value=True):
        await button.callback(mock_interaction)

    mock_interaction.response.defer.assert_awaited_once()
    mock_interaction.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_log_dismiss_view_non_admin_rejected():
    view = AdminLogDismissView()
    button = view.children[0]

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.response.send_message = AsyncMock()
    mock_interaction.message = MagicMock()
    mock_interaction.message.delete = AsyncMock()

    with patch("apps.obx_tasks.bot.permissions.is_admin", return_value=False):
        await button.callback(mock_interaction)

    mock_interaction.response.send_message.assert_awaited_once()
    assert "Administrator permission required" in mock_interaction.response.send_message.call_args[0][0]
    mock_interaction.message.delete.assert_not_called()


@pytest.mark.asyncio
async def test_send_admin_log_event_attaches_dismiss_view(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_admin_test", "admin", "901", "admin")

    mock_guild = MagicMock(spec=discord.Guild, id="g_admin_test")
    mock_ch = MagicMock(spec=discord.TextChannel, id=901)
    mock_ch.send = AsyncMock()
    mock_guild.get_channel.return_value = mock_ch

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        await send_admin_log_event(
            guild=mock_guild,
            title="[AUDIT] Test Event",
            description="Details of test event",
            color=0x00FF00,
        )

    mock_ch.send.assert_awaited_once()
    send_kw = mock_ch.send.call_args[1]
    assert isinstance(send_kw.get("view"), AdminLogDismissView)


@pytest.mark.asyncio
async def test_auction_edit_preserves_raider_role_tag_and_suppresses_reping(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_auc_preserve", "auctions", "801", "admin")

    auc = Auction(
        id=uuid.uuid4(),
        title="Role Preserved Auction",
        description="WL auction testing role mention",
        reward_title="1x Pass",
        auction_type=AuctionType.GTD,
        total_slots=1,
        price_or_min_bid=10,
        ends_at=datetime.now(timezone.utc) + timedelta(hours=1),
        created_by="admin_1",
    )
    db_session.add(auc)
    db_session.commit()

    ch_service.record_published_message("g_auc_preserve", "AUCTION_ANNOUNCEMENT", "801", "12345", source_id=str(auc.id))

    mock_guild = MagicMock(spec=discord.Guild, id="g_auc_preserve")
    mock_msg = MagicMock(spec=discord.Message, id=12345)
    mock_msg.content = "<@&9988776655>"
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=801, name="auctions")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.resolve_raider_role", return_value=("9988776655", MagicMock(id=9988776655))):
        ok, res = await announce_auction(auc, mock_guild, mock_bot)

    assert ok is True
    mock_msg.edit.assert_awaited_once()
    edit_kw = mock_msg.edit.call_args[1]
    # The role mention content is PRESERVED, not None!
    assert edit_kw["content"] == "<@&9988776655>"
    # AllowedMentions is none so no re-ping occurs
    assert edit_kw["allowed_mentions"].roles is False
    assert edit_kw["allowed_mentions"].everyone is False


@pytest.mark.asyncio
async def test_task_edit_preserves_raider_role_tag_and_suppresses_reping(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_task_preserve", "tasks", "802", "admin")

    from apps.obx_tasks.services.task_service import TaskService
    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Role Preserved Task",
        description="Task testing role mention preservation",
        task_type="LIKE",
        target_url="https://x.com/obx/status/999",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
    )

    ch_service.record_published_message("g_task_preserve", "TASK_ANNOUNCEMENT", "802", "54321", source_id=str(task.id))

    mock_guild = MagicMock(spec=discord.Guild, id="g_task_preserve")
    mock_msg = MagicMock(spec=discord.Message, id=54321)
    mock_msg.content = "<@&9988776655>"
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=802, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.resolve_raider_role", return_value=("9988776655", MagicMock(id=9988776655))):
        ok, res = await announce_task(task, mock_guild, mock_bot)

    assert ok is True
    mock_msg.edit.assert_awaited_once()
    edit_kw = mock_msg.edit.call_args[1]
    # The role mention content is PRESERVED, not None!
    assert edit_kw["content"] == "<@&9988776655>"
    # AllowedMentions is none so no re-ping occurs
    assert edit_kw["allowed_mentions"].roles is False
    assert edit_kw["allowed_mentions"].everyone is False
