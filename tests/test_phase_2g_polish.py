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
    announce_auction_ending_soon,
    TaskAnnouncementCardView,
)
from apps.obx_tasks.bot.auction_views import (
    build_auction_notification_embed,
    AuctionNotificationCardView,
)
from apps.obx_tasks.bot.leaderboard_views import LeaderboardView
from apps.obx_tasks.bot.notification_service import (
    build_reward_celebration_embed,
    RewardCelebrationView,
)


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


def test_task_announcement_card_presentation_and_cleanliness():
    """Verify single-card task announcement design, hierarchy, and zero technical clutter."""
    task = MagicMock(spec=Task)
    task.id = uuid.uuid4()
    task.title = "Spread The Word on X"
    task.description = "Quote tweet the official announcement with #OBX"
    task.task_type = "RETWEET"
    task.target_url = "https://x.com/obx/status/987654321"
    task.platform = "X"
    task.reward_per_user = 25
    task.total_reward_pool = 500
    task.distributed_reward = 50
    task.max_approvals = 20
    task.approved_count = 2
    task.status = TaskStatus.ACTIVE
    task.ends_at = datetime.now(timezone.utc) + timedelta(hours=3)
    task.preview_text_override = None
    task.preview_title_override = None
    task.preview_author_override = None
    task.preview_image_override = None
    task.preview_description = None
    task.preview_author = None
    task.preview_image_url = None

    embed = build_task_announcement_embed(task)

    # 1. Headline and hierarchy: Minimal card has embed.title is None
    assert embed.title is None
    assert "Retweet the target post on X" in embed.description
    assert "💎 **25 OBX**" in embed.description
    assert "18 SPOTS" in embed.description

    # 2. Strict cleanliness: Zero technical backend jargon & Zero ugly system fields
    assert "ACTIVE" not in embed.description
    assert "💎 **REWARD**" not in embed.description
    assert "⏳ **TIME REMAINING**" not in embed.description
    assert "👥 **SPOTS**" not in embed.description
    assert str(task.id) not in embed.description
    assert embed.footer.text is None or not embed.footer.text

    # 3. View buttons: Open Task link + Complete Mission action
    view = TaskAnnouncementCardView(task_id=str(task.id), is_active=True, target_url=task.target_url)
    assert len(view.children) == 2
    link_btn = view.children[0]
    assert link_btn.label.upper() == "OPEN TASK"
    assert link_btn.url == task.target_url
    assert link_btn.emoji is None

    action_btn = view.children[1]
    assert action_btn.label.upper() == "COMPLETE MISSION"
    assert action_btn.emoji is None
    assert action_btn.style == discord.ButtonStyle.success
    assert action_btn.custom_id == f"obx:task_card:verify:{task.id}"


@pytest.mark.asyncio
async def test_single_message_task_announcement_and_smart_notifications(db_session):
    """Verify task announcement sends exactly 1 message and suppresses ping on edit."""
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_p2g_task", "tasks", "101", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Phase 2G Like Mission",
        description="Like the launch post",
        task_type="LIKE",
        target_url="https://x.com/obx/status/112233",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_p2g",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_p2g_task")
    mock_msg = MagicMock(id=555001)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=101, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. Initial Publish: Exactly 1 message sent
        ok, res = await announce_task(task, mock_guild, mock_bot)
        assert ok is True
        assert mock_ch.send.call_count == 1, "Must be strictly 1 message (no secondary raw URL message)"

        send_kwargs = mock_ch.send.call_args[1]
        content = send_kwargs["content"]
        expected_raid_ping = f"<@&{get_settings().RAID_ROLE_ID}>"
        assert expected_raid_ping in content
        assert "@everyone" not in content
        assert "NEW OBX MISSION AVAILABLE" not in content
        assert "An opportunity has just appeared" not in content

        # 2. In-place Edit: Message edited with content=None (zero repeated ping)
        task_service.edit_task(task.id, changed_by="admin_p2g", description="Updated description")
        ok_edit, res_edit = await announce_task(task, mock_guild, mock_bot)
        assert ok_edit is True
        assert mock_ch.send.call_count == 1  # No new sends
        mock_msg.edit.assert_called_once()
        assert mock_msg.edit.call_args[1].get("content") is None


def test_auction_announcement_gtd_and_fcfs_cards():
    """Verify GTD and FCFS auction announcements and button polish."""
    # GTD ranked bid auction
    gtd_auc = MagicMock(spec=Auction)
    gtd_auc.id = uuid.uuid4()
    gtd_auc.title = "Monad Early Access"
    gtd_auc.reward_title = "Guaranteed WL Spot"
    gtd_auc.description = "Top 10 bids earn allocation"
    gtd_auc.auction_type = AuctionType.GTD
    gtd_auc.total_slots = 10
    gtd_auc.price_or_min_bid = 150
    gtd_auc.status = AuctionStatus.ACTIVE
    gtd_auc.ends_at = datetime.now(timezone.utc) + timedelta(days=1)
    gtd_auc.image_url = None
    gtd_auc.external_url = "https://monad.xyz"

    gtd_auc.preview_x_handle = None
    gtd_auc.preview_x_display_name = None
    gtd_auc.preview_x_avatar_url = None
    gtd_auc.preview_x_banner_url = None

    standings = {"total_bidders": 5, "winning_cutoff": 200}
    embed_gtd = build_auction_notification_embed(gtd_auc, standings)

    assert embed_gtd.title == "🎟️ WHITELIST AUCTION"
    assert "WINNERS" in embed_gtd.description
    assert "10" in embed_gtd.description
    assert "MIN BID" in embed_gtd.description
    assert "150 OBX" in embed_gtd.description
    assert "ENDS" in embed_gtd.description
    assert embed_gtd.footer.text is None or not embed_gtd.footer.text

    view_gtd = AuctionNotificationCardView(
        auction_id=str(gtd_auc.id),
        is_active=True,
        is_fcfs=False,
        external_url=gtd_auc.external_url,
    )
    btn_labels = [b.label for b in view_gtd.children]
    assert "BID" in btn_labels
    assert "EDIT BID" in btn_labels
    assert "MY BID" in btn_labels

    # Any auction renders standard ranked bidding UI
    auc_multi = MagicMock(spec=Auction)
    auc_multi.id = uuid.uuid4()
    auc_multi.title = "Berachain VIP WL"
    auc_multi.reward_title = "Guaranteed Whitelist"
    auc_multi.description = "Ranked bidding allocation"
    auc_multi.auction_type = AuctionType.GTD
    auc_multi.total_slots = 25
    auc_multi.price_or_min_bid = 300
    auc_multi.status = AuctionStatus.ACTIVE
    auc_multi.ends_at = datetime.now(timezone.utc) + timedelta(days=2)
    auc_multi.image_url = None
    auc_multi.external_url = None

    auc_multi.preview_x_handle = None
    auc_multi.preview_x_display_name = None
    auc_multi.preview_x_avatar_url = None
    auc_multi.preview_x_banner_url = None

    embed_multi = build_auction_notification_embed(auc_multi)
    assert embed_multi.title == "🎟️ WHITELIST AUCTION"
    assert "WINNERS" in embed_multi.description
    assert "25" in embed_multi.description
    assert "MIN BID" in embed_multi.description
    assert "300 OBX" in embed_multi.description

    view_multi = AuctionNotificationCardView(
        auction_id=str(auc_multi.id),
        is_active=True,
    )
    multi_btn_labels = [b.label for b in view_multi.children]
    assert "BID" in multi_btn_labels
    assert "EDIT BID" in multi_btn_labels
    assert "MY BID" in multi_btn_labels


@pytest.mark.asyncio
async def test_auction_smart_notifications_and_winner_celebration(db_session):
    """Verify auction and winner smart notification content and winner card format."""
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_p2g_auc", "auctions", "201", "admin")
    ch_service.update_guild_channel("g_p2g_auc", "winners", "202", "admin")

    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="Eclipse Genesis WL",
        reward_title="Genesis Spot",
        description="Ranked bidding allocation",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_p2g",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_p2g_auc")
    mock_msg_auc = MagicMock(id=666001)
    mock_msg_auc.edit = AsyncMock()
    mock_ch_auc = MagicMock(spec=discord.TextChannel, id=201, name="auctions")
    mock_ch_auc.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_auc.send = AsyncMock(return_value=mock_msg_auc)
    mock_ch_auc.fetch_message = AsyncMock(return_value=mock_msg_auc)

    mock_msg_win = MagicMock(id=666002)
    mock_msg_win.edit = AsyncMock()
    mock_ch_win = MagicMock(spec=discord.TextChannel, id=202, name="winners")
    mock_ch_win.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch_win.send = AsyncMock(return_value=mock_msg_win)
    mock_ch_win.fetch_message = AsyncMock(return_value=mock_msg_win)

    def mock_get_channel(cid):
        return mock_ch_auc if cid == 201 else mock_ch_win
    mock_guild.get_channel.side_effect = mock_get_channel

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. New Auction announcement notification
        expected_raid_ping = f"<@&{get_settings().RAID_ROLE_ID}>"
        ok_a, _ = await announce_auction(auc, mock_guild, mock_bot)
        assert ok_a is True
        auc_content = mock_ch_auc.send.call_args[1]["content"]
        assert auc_content == expected_raid_ping
        assert "@everyone" not in auc_content
        assert "NEW OBX AUCTION IS LIVE" not in auc_content
        assert "A new whitelist opportunity is now available" not in auc_content

        # 2. Auction Ending Warning notification
        auc.ends_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        ok_warn, _ = await announce_auction_ending_soon(auc, mock_guild, mock_bot)
        assert ok_warn is True
        warn_content = mock_ch_auc.send.call_args[1]["content"]
        assert warn_content == expected_raid_ping
        assert "@everyone" not in warn_content
        assert "OBX AUCTION CLOSING SOON" not in warn_content

        # Repeat warning must be idempotent (already posted)
        ok_warn_dup, _ = await announce_auction_ending_soon(auc, mock_guild, mock_bot)
        assert ok_warn_dup is True

        # 3. Winner Announcement notification
        w1 = MagicMock(spec=AuctionBid, discord_user_id="user_alpha", bid_amount=500)
        w2 = MagicMock(spec=AuctionBid, discord_user_id="user_beta", bid_amount=450)
        ok_w, _ = await announce_auction_winners(auc, [w1, w2], 2, mock_guild, mock_bot)
        assert ok_w is True

        win_content = mock_ch_win.send.call_args[1]["content"]
        assert win_content == expected_raid_ping
        assert "@everyone" not in win_content
        assert "OBX RESULTS ARE IN" not in win_content
        assert "The winners have been confirmed" not in win_content

        win_embed = mock_ch_win.send.call_args[1]["embed"]
        assert win_embed.title == "🏆 AUCTION RESULTS"
        assert "🎟️ **SPOTS AWARDED**" in win_embed.description
        assert "🏆 **WINNERS**" in win_embed.description
        assert "user_alpha" in win_embed.description
        assert "user_beta" in win_embed.description

        win_view = mock_ch_win.send.call_args[1]["view"]
        assert len(win_view.children) == 1
        assert win_view.children[0].label == "View My Result"
        assert win_view.children[0].emoji is None


def test_task_completion_reward_celebration_dm():
    """Verify task completion private DM redesign format."""
    task = MagicMock(spec=Task)
    task.title = "Viral Launch Raid"
    task.notification_type = "DEFAULT"
    task.custom_notification_template = None

    result = build_reward_celebration_embed(
        task=task,
        discord_user_id="778899",
        reward_amount=50,
        new_balance=1250,
        display_name="CryptoWhale",
    )
    assert result is not None
    embed, view = result

    # Header and celebratory content
    assert embed.title == "✨ MISSION COMPLETE"
    assert "Congratulations, CryptoWhale!" in embed.description
    assert "**Viral Launch Raid**" in embed.description
    assert "💎 **REWARD EARNED**" in embed.description
    assert "`+50 OBX`" in embed.description
    assert "💼 **NEW BALANCE**" in embed.description
    assert "`1,250 OBX`" in embed.description
    assert embed.footer.text == "OBX Community Rewards"

    # Action view button
    assert len(view.children) == 1
    assert view.children[0].label == "My Wallet"
    assert view.children[0].emoji is None
    assert view.children[0].custom_id == "obx:dashboard:my_balance"


def test_leaderboard_view_no_home_or_activity_buttons():
    """Verify public leaderboard view has no Home or Activity buttons."""
    view = LeaderboardView()
    labels = [b.label for b in view.children if hasattr(b, "label")]
    assert "Home" not in labels
    assert "Activity" not in labels
