import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta
import discord

from packages.shared.config import get_settings
from packages.shared.enums import TaskStatus, TaskType, AuctionType, AuctionStatus
from packages.database.models.task import Task
from packages.database.models.auction import Auction, AuctionBid
from apps.obx_tasks.services.task_service import TaskService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.bot.announcement_service import (
    build_task_announcement_embed,
    announce_task,
    announce_auction,
    announce_auction_winners,
    TaskAnnouncementCardView,
)
from apps.obx_tasks.bot.auction_views import build_auction_notification_embed, AuctionNotificationCardView
from apps.obx_tasks.bot.dashboard_views import AdminCreateTaskTypeSelectView, AdminCreateTaskModal


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


def test_task_headline_dynamic_mapping():
    cases = [
        ("LIKE", "❤️ LIKE MISSION", "X (Twitter)"),
        ("RETWEET", "🔁 REPOST MISSION", "X (Twitter)"),
        ("COMMENT", "💬 COMMENT MISSION", "X (Twitter)"),
        ("FOLLOW", "👥 FOLLOW MISSION", "X (Twitter)"),
        ("JOIN_DISCORD", "📣 DISCORD MISSION", "Discord"),
        ("CUSTOM_TASK", "📝 OBX MISSION", "Web"),
    ]

    for t_type, expected_headline, expected_platform in cases:
        mock_task = MagicMock(spec=Task)
        mock_task.id = uuid.uuid4()
        mock_task.title = f"Utopia {t_type.capitalize()} Campaign"
        mock_task.description = "Complete this official community goal."
        mock_task.task_type = t_type
        if t_type == "JOIN_DISCORD":
            mock_task.target_url = "https://discord.gg/obx"
            mock_task.platform = "Discord"
        elif t_type == "CUSTOM_TASK":
            mock_task.target_url = "https://medium.com/obx/article"
            mock_task.platform = "Web"
        else:
            mock_task.target_url = "https://x.com/obx/status/1"
            mock_task.platform = "X"
        mock_task.reward_per_user = 10
        mock_task.total_reward_pool = 100
        mock_task.distributed_reward = 0
        mock_task.max_approvals = 10
        mock_task.approved_count = 0
        mock_task.status = TaskStatus.ACTIVE
        mock_task.ends_at = None

        embed = build_task_announcement_embed(mock_task)

        # 1. No large task headers or duplicated type labels
        assert embed.title is None
        # 2. Description has core info
        assert "10 OBX" in embed.description
        assert "10 SPOTS" in embed.description
        # 3. Clean compact layout: NO system active status or duplicated mission headline
        assert "ACTIVE" not in embed.description
        assert "LIKE MISSION" not in embed.description
        assert "NEW OBX MISSION" not in embed.description
        # 4. Zero technical clutter: NO internal UUID, NO Double-Entry Vault, NO technical footer
        assert str(mock_task.id) not in embed.description
        assert embed.footer.text is None or not embed.footer.text


def test_task_card_buttons_active_and_disabled_states():
    t_id = "test-task-123"

    # Active view
    v_active = TaskAnnouncementCardView(task_id=t_id, is_active=True, target_url="https://x.com/obx/1")
    btn_labels = [b.label.upper() for b in v_active.children if b.label]
    assert "OPEN TASK" in btn_labels
    assert "COMPLETE MISSION" in btn_labels
    verify_btn = [b for b in v_active.children if getattr(b, "custom_id", None) == f"obx:task_card:verify:{t_id}"][0]
    assert verify_btn.disabled is False
    assert verify_btn.emoji is None

    # Cancelled view
    v_cancel = TaskAnnouncementCardView(task_id=t_id, is_active=False, is_cancelled=True)
    cancel_btn = [b for b in v_cancel.children if "CANCEL" in b.label.upper()][0]
    assert cancel_btn.disabled is True

    # Expired view
    v_exp = TaskAnnouncementCardView(task_id=t_id, is_active=False, is_expired=True)
    exp_btn = [b for b in v_exp.children if "EXPIRED" in b.label.upper()][0]
    assert exp_btn.disabled is True


@pytest.mark.asyncio
async def test_announce_task_everyone_ping_on_create_only(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_everyone_test", "tasks", "111", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Everyone Ping Task",
        description="Like the post and earn OBX",
        task_type="LIKE",
        target_url="https://x.com/obx/status/1",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_everyone_test")
    mock_msg = MagicMock(id=999111)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=111, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # First send: must include RAID_ROLE_ID ping (never @everyone)
        ok, msg = await announce_task(task, mock_guild, mock_bot)
        assert ok is True
        first_send_call = mock_ch.send.call_args_list[0]
        expected_ping = f"<@&{get_settings().RAID_ROLE_ID}>"
        assert expected_ping in first_send_call[1]["content"]
        assert "@everyone" not in first_send_call[1]["content"]
        assert "NEW OBX MISSION AVAILABLE" not in first_send_call[1]["content"]
        assert first_send_call[1]["content"].strip() == expected_ping

        # Edit task & announce again: must NOT re-ping (content=None)
        task_service.edit_task(task.id, changed_by="admin_1", description="Updated description")
        ok2, msg2 = await announce_task(task, mock_guild, mock_bot)
        assert ok2 is True
        mock_msg.edit.assert_called_once()
        assert mock_msg.edit.call_args[1].get("content") is None


@pytest.mark.asyncio
async def test_announce_auction_and_winners_everyone_ping(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_auc_test", "auctions", "222", "admin")
    ch_service.update_guild_channel("g_auc_test", "winners", "333", "admin")

    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="Test Whitelist Auction",
        reward_title="Alpha Pass",
        description="Ranked bidding allocation",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_auc_test")
    mock_msg_auc = MagicMock(id=888111)
    mock_msg_auc.edit = AsyncMock()
    mock_ch_auc = MagicMock(spec=discord.TextChannel, id=222, name="auctions")
    mock_ch_auc.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_auc.send = AsyncMock(return_value=mock_msg_auc)
    mock_ch_auc.fetch_message = AsyncMock(return_value=mock_msg_auc)

    mock_msg_win = MagicMock(id=888222)
    mock_msg_win.edit = AsyncMock()
    mock_ch_win = MagicMock(spec=discord.TextChannel, id=333, name="winners")
    mock_ch_win.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_win.send = AsyncMock(return_value=mock_msg_win)
    mock_ch_win.fetch_message = AsyncMock(return_value=mock_msg_win)

    def mock_get_channel(cid):
        return mock_ch_auc if cid == 222 else mock_ch_win
    mock_guild.get_channel.side_effect = mock_get_channel

    mock_bot = MagicMock(spec=discord.Client)
    expected_ping = f"<@&{get_settings().RAID_ROLE_ID}>"

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. First auction announcement: pings strictly RAID_ROLE_ID (zero @everyone, zero generic text)
        ok_a, _ = await announce_auction(auc, mock_guild, mock_bot)
        assert ok_a is True
        assert mock_ch_auc.send.call_args[1]["content"] == expected_ping
        assert "@everyone" not in mock_ch_auc.send.call_args[1]["content"]
        assert "NEW OBX AUCTION IS LIVE" not in mock_ch_auc.send.call_args[1]["content"]
        assert "A new whitelist opportunity is now available" not in mock_ch_auc.send.call_args[1]["content"]

        # 2. In-place auction update: no ping
        ok_a2, _ = await announce_auction(auc, mock_guild, mock_bot)
        assert ok_a2 is True
        assert mock_msg_auc.edit.call_args[1].get("content") is None

        # 3. Winner announcement: pings strictly RAID_ROLE_ID (zero @everyone, zero generic text)
        winner_mock = MagicMock(spec=AuctionBid, discord_user_id="user_w1", bid_amount=250)
        ok_w, _ = await announce_auction_winners(auc, [winner_mock], 1, mock_guild, mock_bot)
        assert ok_w is True
        assert mock_ch_win.send.call_args[1]["content"] == expected_ping
        assert "@everyone" not in mock_ch_win.send.call_args[1]["content"]
        assert "OBX RESULTS ARE IN" not in mock_ch_win.send.call_args[1]["content"]
        assert "The winners have been confirmed" not in mock_ch_win.send.call_args[1]["content"]

        # 4. Winner update in place: no ping
        ok_w2, _ = await announce_auction_winners(auc, [winner_mock], 1, mock_guild, mock_bot)
        assert ok_w2 is True
        assert mock_msg_win.edit.call_args[1].get("content") is None


def test_admin_create_task_type_select_view():
    view = AdminCreateTaskTypeSelectView()
    labels = [b.label for b in view.children]
    assert "Like Task" in labels
    assert "Repost Task" in labels
    assert "Comment Task" in labels
    assert "Follow Task" in labels
    assert "Discord Task" in labels
    assert "Custom Task" in labels

    # Check modal title is hardcoded to ⚡ CREATE TASK
    modal_like = AdminCreateTaskModal(task_type="LIKE")
    assert modal_like.title == "⚡ CREATE TASK"
    assert modal_like.task_type == "LIKE"

    modal_repost = AdminCreateTaskModal(task_type="RETWEET")
    assert modal_repost.title == "⚡ CREATE TASK"
    assert modal_repost.task_type == "RETWEET"
