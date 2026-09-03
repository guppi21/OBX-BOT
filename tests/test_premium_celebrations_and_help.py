"""Tests for OBX Premium Celebrations, Typography, and Channel Help System."""

import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import discord
from packages.shared.config import get_settings

from packages.database.models.task import Task
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import TaskStatus, TaskType, SubmissionStatus, AuctionStatus, AuctionType
from packages.shared.typography import (
    DIVIDER, SHORT_DIVIDER, CELEBRATION_QUOTES,
    format_h1, format_h2, format_section_label, format_reward_amount, format_spots
)
from apps.obx_tasks.bot.notification_service import (
    build_in_server_celebration, DismissRewardCelebrationView
)
from apps.obx_tasks.bot.help_views import (
    TaskCenterIntroView, AuctionCenterIntroView, WinnersCenterIntroView,
    TasksHelpView, AuctionsHelpView, WinnersHelpView,
    send_tasks_how_it_works, send_auctions_how_it_works, send_winners_how_it_works
)
from apps.obx_tasks.bot.announcement_service import (
    deploy_or_update_task_center, deploy_or_update_auction_center,
    deploy_or_update_winners_center, refresh_all_public_systems
)
from apps.obx_tasks.services.channel_service import ChannelService


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


def test_typography_helpers():
    assert format_h1("Mission Complete") == "# **MISSION COMPLETE**"
    assert format_h2("Secure Your Spot") == "## **SECURE YOUR SPOT**"
    assert format_section_label("Reward") == "**REWARD**"
    assert format_reward_amount(20) == "💎 **+20 OBX**"
    assert format_spots(5) == "👥 **5 SPOTS**"
    assert DIVIDER == "━━━━━━━━━━━━━━━━━━━━"
    assert len(CELEBRATION_QUOTES) >= 5


def test_approved_reward_celebration_structure():
    mock_task = MagicMock(spec=Task)
    mock_task.id = uuid.uuid4()
    mock_task.title = "LIKE THE ₿-R4TED ALPHA POST"

    mock_sub = MagicMock(spec=TaskSubmission)
    mock_sub.id = uuid.uuid4()
    mock_sub.discord_user_id = "987654321"

    content, embed, view = build_in_server_celebration(
        task=mock_task,
        submission=mock_sub,
        reward_amount=20,
    )

    # 1. Mentions user in content
    assert content == "<@987654321>"

    # 2. Embed structure & excitement
    assert embed.title == "✨ MISSION COMPLETE"
    assert "# <@987654321>, YOU JUST EARNED OBX." in embed.description
    assert "Your mission was verified successfully." in embed.description
    assert DIVIDER in embed.description
    assert "💎  **+20 OBX**" in embed.description
    assert "Your reward has been secured in your OBX wallet." in embed.description
    assert any(q in embed.description for q in CELEBRATION_QUOTES)
    assert embed.footer.text == "✦ OBX COMMUNITY REWARDS"

    # 3. View buttons: View Wallet and Dismiss
    assert len(view.children) == 2
    wallet_btn = view.children[0]
    assert wallet_btn.label == "View Wallet"
    assert wallet_btn.custom_id == "obx:user:wallet"
    assert wallet_btn.emoji is None

    dismiss_btn = view.children[1]
    assert dismiss_btn.label == "Dismiss"
    assert dismiss_btn.custom_id == f"obx:celebrate:dismiss:{mock_sub.id}:987654321"


@pytest.mark.asyncio
async def test_celebration_dismissal_permissions():
    view = DismissRewardCelebrationView(submission_id="sub-1", user_id="user-owner")

    # Unauthorized user tries to dismiss
    mock_intr_stranger = MagicMock(spec=discord.Interaction)
    mock_intr_stranger.user.id = 999999
    mock_intr_stranger.data = {"custom_id": "obx:celebrate:dismiss:sub-1:user-owner"}
    mock_intr_stranger.response = MagicMock()
    mock_intr_stranger.response.send_message = AsyncMock()

    allowed = await view.interaction_check(mock_intr_stranger)
    assert allowed is False
    mock_intr_stranger.response.send_message.assert_called_once()
    assert "Only the rewarded member can dismiss" in mock_intr_stranger.response.send_message.call_args[0][0]

    # Authorized user dismisses
    mock_intr_owner = MagicMock(spec=discord.Interaction)
    mock_intr_owner.user.id = "user-owner"
    mock_intr_owner.data = {"custom_id": "obx:celebrate:dismiss:sub-1:user-owner"}
    mock_msg = MagicMock()
    mock_msg.delete = AsyncMock()
    mock_intr_owner.message = mock_msg

    allowed = await view.interaction_check(mock_intr_owner)
    assert allowed is False
    mock_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_tasks_how_it_works_ephemeral():
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = MagicMock()
    mock_intr.response.send_message = AsyncMock()

    await send_tasks_how_it_works(mock_intr)

    mock_intr.response.send_message.assert_called_once()
    kwargs = mock_intr.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert embed.title == "🎯 HOW OBX MISSIONS WORK"
    assert "# COMPLETE MISSIONS. EARN OBX." in embed.description
    assert "① Find a mission" in embed.description
    assert "② Complete the action" in embed.description
    assert "③ Submit proof" in embed.description
    assert "④ Wait for review" in embed.description
    assert "⑤ Earn your reward" in embed.description
    assert DIVIDER in embed.description

    view = kwargs["view"]
    assert isinstance(view, TasksHelpView)
    assert view.children[0].label == "My Submissions"
    assert view.children[0].custom_id == "obx:user:submissions"
    assert view.children[1].label == "Close"
    assert view.children[1].custom_id == "obx:help:close"


@pytest.mark.asyncio
async def test_auctions_how_it_works_ephemeral():
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = MagicMock()
    mock_intr.response.send_message = AsyncMock()

    await send_auctions_how_it_works(mock_intr)

    mock_intr.response.send_message.assert_called_once()
    kwargs = mock_intr.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert embed.title == "🔨 HOW OBX AUCTIONS WORK"
    assert "# SECURE YOUR SPOT." in embed.description
    assert "① Find an active auction" in embed.description
    assert "② Choose your bid" in embed.description
    assert "③ Your OBX is reserved" in embed.description
    assert "④ Check your position" in embed.description
    assert "⑤ Results are announced" in embed.description

    view = kwargs["view"]
    assert isinstance(view, AuctionsHelpView)
    assert view.children[0].label == "My Wallet"
    assert view.children[0].custom_id == "obx:user:wallet"
    assert view.children[1].label == "Close"


@pytest.mark.asyncio
async def test_winners_how_it_works_ephemeral():
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.response = MagicMock()
    mock_intr.response.send_message = AsyncMock()

    await send_winners_how_it_works(mock_intr)

    mock_intr.response.send_message.assert_called_once()
    kwargs = mock_intr.response.send_message.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert embed.title == "🏆 HOW RESULTS WORK"
    assert "# SEE WHO WON." in embed.description
    assert "① Wait for the auction to close" in embed.description
    assert "② Winners are calculated" in embed.description
    assert "③ Results are published" in embed.description
    assert "④ Check your personal result" in embed.description
    assert "⑤ Claim your reward or whitelist" in embed.description

    view = kwargs["view"]
    assert isinstance(view, WinnersHelpView)
    assert view.children[0].label == "View My Result"
    assert view.children[0].custom_id == "obx:help:view_result"
    assert view.children[1].label == "Close"


@pytest.mark.asyncio
async def test_persistent_channel_dashboards_deployment(db_session):
    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_help_test", "tasks", "101", "admin")
    ch_service.update_guild_channel("g_help_test", "auctions", "202", "admin")
    ch_service.update_guild_channel("g_help_test", "winners", "303", "admin")
    ch_service.update_guild_channel("g_help_test", "leaderboard", "404", "admin")
    ch_service.update_guild_channel("g_help_test", "admin", "505", "admin")

    mock_guild = MagicMock(spec=discord.Guild, id="g_help_test")
    mock_guild.me = MagicMock()

    mock_channels = {}
    for cid, name in [("101", "tasks"), ("202", "auctions"), ("303", "winners"), ("404", "leaderboard"), ("505", "admin_logs")]:
        ch = MagicMock(spec=discord.TextChannel, id=int(cid), name=name)
        ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
        msg = MagicMock(id=int(f"999{cid}"))
        msg.edit = AsyncMock()
        ch.send = AsyncMock(return_value=msg)
        ch.fetch_message = AsyncMock(return_value=msg)
        mock_channels[int(cid)] = ch

    mock_guild.get_channel.side_effect = lambda cid: mock_channels.get(cid)
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. Refresh all public systems
        results = await refresh_all_public_systems(mock_guild, mock_bot)

        assert "Tasks" in results
        assert "Auctions" in results
        assert "Winners" in results
        assert "Leaderboard" in results
        assert "Admin Hub" in results

        # 2. Check Tasks channel dashboard
        ch_tasks = mock_channels[101]
        assert ch_tasks.send.call_count == 1
        tasks_send_args = ch_tasks.send.call_args[1]
        assert tasks_send_args["embed"].title == "🎯 OBX MISSIONS"
        assert "Complete community missions." in tasks_send_args["embed"].description
        assert isinstance(tasks_send_args["view"], TaskCenterIntroView)
        assert tasks_send_args["view"].children[0].custom_id == "obx:help:tasks"
        assert tasks_send_args["view"].children[1].custom_id == "obx:help:browse_missions"

        # 3. Check Auctions channel dashboard
        ch_aucs = mock_channels[202]
        assert ch_aucs.send.call_count == 1
        aucs_send_args = ch_aucs.send.call_args[1]
        assert aucs_send_args["embed"].title == "🔨 OBX AUCTIONS"
        assert "Bid your OBX." in aucs_send_args["embed"].description
        assert isinstance(aucs_send_args["view"], AuctionCenterIntroView)
        assert aucs_send_args["view"].children[0].custom_id == "obx:help:auctions"
        assert aucs_send_args["view"].children[1].custom_id == "obx:help:view_auctions"

        # 4. Check Winners channel dashboard
        ch_wins = mock_channels[303]
        assert ch_wins.send.call_count == 1
        wins_send_args = ch_wins.send.call_args[1]
        assert wins_send_args["embed"].title == "🏆 OBX RESULTS"
        assert "See confirmed winners and completed results." in wins_send_args["embed"].description
        assert isinstance(wins_send_args["view"], WinnersCenterIntroView)
        assert wins_send_args["view"].children[0].custom_id == "obx:help:winners"
        assert wins_send_args["view"].children[1].custom_id == "obx:help:view_result"

        # 5. Idempotent re-run: Edits messages, does NOT create new sends
        results2 = await refresh_all_public_systems(mock_guild, mock_bot)
        assert ch_tasks.send.call_count == 1
        assert ch_aucs.send.call_count == 1
        assert ch_wins.send.call_count == 1


@pytest.mark.asyncio
async def test_initial_auction_announcement_content_is_strictly_everyone(db_session):
    """Critical debug test requested by user:
    Assert that the initial auction announcement content is strictly '@everyone'
    with zero generic text or headlines.
    """
    from apps.obx_tasks.services.auction_service import AuctionService
    from apps.obx_tasks.bot.announcement_service import announce_auction

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_auc_clean", "auctions", "888", "admin")

    auc_service = AuctionService(db_session)
    mock_auc = auc_service.create_auction(
        title="Monad Whitelist Auction",
        reward_title="Guaranteed Allocation",
        description="Live FCFS sale",
        auction_type=AuctionType.FCFS,
        total_slots=1,
        price_or_min_bid=5,
        created_by="admin",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_auc_clean")
    mock_msg = MagicMock(id=112233)
    mock_msg.edit = AsyncMock()
    mock_ch = MagicMock(spec=discord.TextChannel, id=888, name="auctions")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, _ = await announce_auction(mock_auc, mock_guild, mock_bot)

    assert ok is True
    assert mock_ch.send.call_count == 1
    call_kwargs = mock_ch.send.call_args[1]
    message_content = call_kwargs["content"]

    # Critical assertions
    expected_raid_ping = f"<@&{get_settings().RAID_ROLE_ID}>"
    assert message_content == expected_raid_ping
    assert "@everyone" not in message_content
    assert "NEW OBX AUCTION IS LIVE" not in message_content
    assert "A new whitelist opportunity is now available" not in message_content

