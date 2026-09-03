import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
import discord
from packages.shared.config import get_settings

from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.bot.channel_views import (
    ChannelConfigView,
    ChannelSelectPromptView,
    AuctionWinnerResultView,
    build_channel_config_embed,
)
from apps.obx_tasks.bot.announcement_service import (
    check_channel_permissions,
    deploy_or_update_task_center,
    deploy_or_update_leaderboard,
    announce_auction_winners,
)
from packages.shared.enums import AuctionType, AuctionStatus, TaskStatus


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


def test_channel_permissions_check():
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_me = MagicMock(spec=discord.Member)

    # All permissions granted
    perms_ok = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_channel.permissions_for.return_value = perms_ok
    valid, missing = check_channel_permissions(mock_channel, mock_me)
    assert valid is True
    assert len(missing) == 0

    # Missing Embed Links
    perms_bad = MagicMock(view_channel=True, send_messages=True, embed_links=False)
    mock_channel.permissions_for.return_value = perms_bad
    valid, missing = check_channel_permissions(mock_channel, mock_me)
    assert valid is False
    assert "Embed Links" in missing


def test_build_channel_config_embed_formatting(db_session):
    service = ChannelService(db_session)
    config = service.get_or_create_guild_config("123")
    config.tasks_channel_id = "555"

    mock_guild = MagicMock(spec=discord.Guild)
    mock_ch = MagicMock(mention="<#555>")
    mock_guild.get_channel.side_effect = lambda cid: mock_ch if cid == 555 else None

    embed = build_channel_config_embed(mock_guild, config)
    assert "<#555>" in embed.fields[0].value
    assert "Not configured" in embed.fields[1].value


@pytest.mark.asyncio
async def test_channel_select_prompt_view_permission_check_and_update(db_session):
    view = ChannelSelectPromptView(channel_key="tasks", display_name="🎯 Tasks Channel")
    mock_selected_ch = MagicMock(spec=discord.TextChannel, id=777888999, mention="<#777888999>")
    mock_selected_ch.name = "obx-tasks"
    mock_selected_ch.send = AsyncMock(return_value=MagicMock(id=999111222))
    perms_ok = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_selected_ch.permissions_for.return_value = perms_ok
    view.select_item._values = [mock_selected_ch]

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.guild = MagicMock(id=123456)
    mock_interaction.guild.me = MagicMock()
    mock_interaction.guild.get_channel.return_value = mock_selected_ch
    mock_interaction.user = MagicMock(id="admin_user_1")
    mock_interaction.client = MagicMock()
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.channel_views.is_admin", return_value=True), \
         patch("apps.obx_tasks.bot.channel_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.select_item.callback(mock_interaction)

    # Check database state
    service = ChannelService(db_session)
    config = service.get_or_create_guild_config("123456")
    assert config.tasks_channel_id == "777888999"


@pytest.mark.asyncio
async def test_auction_winner_result_view_winner_and_non_winner(db_session):
    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="Public WL Drop",
        reward_title="Pass",
        description="Top 1 wins",
        auction_type=AuctionType.GTD,
        total_slots=1,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    # Place winning and losing bid
    auc_service.wallet_service.get_or_create_user("win_user")
    auc_service.wallet_service.credit("win_user", 1000, "test", "w1")
    auc_service.wallet_service.get_or_create_user("lose_user")
    auc_service.wallet_service.credit("lose_user", 1000, "test", "l1")

    auc_service.place_or_update_gtd_bid(auc.id, "win_user", 500)
    auc_service.place_or_update_gtd_bid(auc.id, "lose_user", 200)

    # Settle auction
    auc_service.settle_and_finalize_auction(auc.id, finalized_by="admin_1")

    view = AuctionWinnerResultView(auction_id=str(auc.id))

    # Test winner lookup
    mock_int_win = MagicMock(spec=discord.Interaction)
    mock_int_win.user = MagicMock(id="win_user")
    mock_int_win.response = AsyncMock()
    mock_int_win.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.channel_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.btn_my_result.callback(mock_int_win)

    mock_int_win.followup.send.assert_called_once()
    win_embed = mock_int_win.followup.send.call_args[1]["embed"]
    assert "YOU WON THE WHITELIST" in win_embed.title

    # Test non-winner lookup
    mock_int_lose = MagicMock(spec=discord.Interaction)
    mock_int_lose.user = MagicMock(id="lose_user")
    mock_int_lose.response = AsyncMock()
    mock_int_lose.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.channel_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await view.btn_my_result.callback(mock_int_lose)

    mock_int_lose.followup.send.assert_called_once()
    lose_embed = mock_int_lose.followup.send.call_args[1]["embed"]
    assert "WL Spot Not Secured" in lose_embed.title
    assert "100% Full Refund Complete" in lose_embed.description


@pytest.mark.asyncio
async def test_refresh_all_public_systems_includes_auctions_channel(db_session):
    from apps.obx_tasks.bot.announcement_service import refresh_all_public_systems

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_all_3", "tasks", "101", "admin")
    ch_service.update_guild_channel("guild_all_3", "leaderboard", "102", "admin")
    ch_service.update_guild_channel("guild_all_3", "auctions", "103", "admin")

    mock_guild = MagicMock(spec=discord.Guild, id=123999)
    mock_guild.id = "guild_all_3"

    mock_ch_tasks = MagicMock(spec=discord.TextChannel, id=101, name="tasks")
    mock_ch_tasks.send = AsyncMock(return_value=MagicMock(id=201))
    mock_ch_tasks.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)

    mock_ch_lb = MagicMock(spec=discord.TextChannel, id=102, name="leaderboard")
    mock_ch_lb.send = AsyncMock(return_value=MagicMock(id=202))
    mock_ch_lb.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)

    mock_ch_auc = MagicMock(spec=discord.TextChannel, id=103, name="auctions")
    mock_ch_auc.send = AsyncMock(return_value=MagicMock(id=203))
    mock_ch_auc.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)

    def get_ch(cid):
        if cid == 101:
            return mock_ch_tasks
        if cid == 102:
            return mock_ch_lb
        if cid == 103:
            return mock_ch_auc
        return None

    mock_guild.get_channel.side_effect = get_ch
    mock_guild.me = MagicMock()
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        results = await refresh_all_public_systems(mock_guild, mock_bot)

    assert "Tasks" in results
    assert "Leaderboard" in results
    assert "Auctions" in results
    assert "✅ Auction Center deployed successfully" in results["Auctions"]

    # Verify published message tracking
    pub_auc = ch_service.get_published_message("guild_all_3", "AUCTION_CENTER")
    assert pub_auc is not None
    assert pub_auc.channel_id == "103"
    assert pub_auc.message_id == "203"


@pytest.mark.asyncio
async def test_auction_center_publishes_empty_state_and_updates_existing(db_session):
    from apps.obx_tasks.bot.announcement_service import deploy_or_update_auction_center

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_auc_empty", "auctions", "888", "admin")

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_auc_empty"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=555111)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=888, name="auctions")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # First deploy: creates new message
        ok1, msg1 = await deploy_or_update_auction_center(mock_guild, mock_bot)
        assert ok1 is True
        mock_ch.send.assert_called_once()
        sent_embed = mock_ch.send.call_args[1]["embed"]
        assert "Bid your OBX" in sent_embed.description or "OBX AUCTIONS" in sent_embed.title

        # Second deploy: edits existing message
        ok2, msg2 = await deploy_or_update_auction_center(mock_guild, mock_bot)
        assert ok2 is True
        assert "refreshed in" in msg2
        mock_msg.edit.assert_called_once()


@pytest.mark.asyncio
async def test_deploy_or_update_admin_hub_enforces_channel_privacy_and_outbox(db_session):
    from apps.obx_tasks.bot.announcement_service import deploy_or_update_admin_hub

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_admin_priv", "admin", "999", "admin")

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_admin_priv"
    mock_guild.default_role = MagicMock()
    mock_admin_role = MagicMock(id=1542982329603985489)
    mock_guild.get_role.return_value = mock_admin_role

    mock_msg = MagicMock(id=888222)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=999, name="obx-admin-logs", overwrites={})
    mock_ch.edit = AsyncMock()
    mock_ch.permissions_for.return_value = MagicMock(
        view_channel=True, send_messages=True, embed_links=True, manage_channels=True, manage_roles=True
    )
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_guild.me = MagicMock()
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, msg = await deploy_or_update_admin_hub(mock_guild, mock_bot)

    assert ok is True
    assert "Admin Hub deployed successfully" in msg
    mock_ch.edit.assert_awaited_once()  # Enforced private channel permissions!
    mock_ch.send.assert_awaited_once()
    sent_embed = mock_ch.send.call_args[1]["embed"]
    assert "ADMINISTRATIVE CONTROL CENTER" in sent_embed.title

    # Verify outbox tracking
    pub_adm = ch_service.get_published_message("guild_admin_priv", "ADMIN_HUB")
    assert pub_adm is not None
    assert pub_adm.channel_id == "999"
    assert pub_adm.message_id == "888222"


@pytest.mark.asyncio
async def test_non_admin_blocked_from_admin_hub_interactions():
    from apps.obx_tasks.bot.dashboard_views import OBXAdminHubView

    admin_view = OBXAdminHubView()
    mock_non_admin = MagicMock(spec=discord.Interaction)
    mock_non_admin.user = MagicMock(spec=discord.Member)
    mock_non_admin.user.guild_permissions.administrator = False
    mock_non_admin.user.guild_permissions.manage_guild = False
    mock_non_admin.user.roles = []
    mock_non_admin.response = AsyncMock()

    with patch("apps.obx_tasks.bot.dashboard_views.is_admin", return_value=False):
        # Click Create Task
        btn_create = [b for b in admin_view.children if b.custom_id == "obx:admin:create_task"][0]
        await btn_create.callback(mock_non_admin)
        mock_non_admin.response.send_message.assert_awaited_once()
        assert "Permission Denied" in mock_non_admin.response.send_message.call_args[0][0]


@pytest.mark.asyncio
async def test_announce_task_publishes_rich_card_with_social_link_unfurl_and_outbox(db_session):
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.bot.announcement_service import announce_task

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_task_ann", "tasks", "777", "admin")
    # Configure role alert
    config = ch_service.get_or_create_guild_config("guild_task_ann")
    config.task_alerts_role_id = "55443322"
    db_session.commit()

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Utopia Raid Task",
        description="Retweet and quote the Utopia announcement with #OBX",
        task_type="RETWEET",
        target_url="https://x.com/obx/status/1234567890",
        reward_per_user=15,
        total_reward_pool=150,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_task_ann"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=998877)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=777, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. First announcement: publishes single rich card with link button (no secondary message)
        ok1, msg1 = await announce_task(task, mock_guild, mock_bot)
        assert ok1 is True
        assert "published to" in msg1
        assert mock_ch.send.call_count == 1

        # First call is the card embed + role ping
        first_call = mock_ch.send.call_args_list[0]
        first_args, first_kwargs = first_call
        expected_raid_ping = f"<@&{get_settings().RAID_ROLE_ID}>"
        assert expected_raid_ping in first_kwargs["content"]
        assert "@everyone" not in first_kwargs["content"]
        embed = first_kwargs["embed"]
        assert embed.title is None
        assert "15 OBX" in embed.description
        assert embed.footer.text is None or not embed.footer.text

        # Verify buttons on view
        view = first_kwargs["view"]
        custom_ids = [b.custom_id for b in view.children if hasattr(b, "custom_id")]
        assert f"obx:task_card:verify:{task.id}" in custom_ids

        # Verify PublishedMessage tracking tracks the main card message
        pub = ch_service.get_published_message("guild_task_ann", "TASK_ANNOUNCEMENT", source_id=str(task.id))
        assert pub is not None
        assert pub.message_id == "998877"

        # 2. Second announcement (repeat/refresh): updates existing message without duplication
        ok2, msg2 = await announce_task(task, mock_guild, mock_bot)
        assert ok2 is True
        assert "updated in" in msg2
        assert mock_ch.send.call_count == 1  # No new sends
        mock_msg.edit.assert_called_once()
        assert mock_msg.edit.call_args[1].get("content") is None


@pytest.mark.asyncio
async def test_announce_task_closed_disables_actions_and_updates_card(db_session):
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.bot.announcement_service import announce_task

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_task_close", "tasks", "777", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Expiring Task",
        description="Task to be closed",
        task_type="LIKE",
        target_url="https://x.com/exp",
        reward_per_user=10,
        total_reward_pool=100,
        created_by="admin_1",
    )

    # Pre-record published message
    ch_service.record_published_message("guild_task_close", "TASK_ANNOUNCEMENT", "777", "443322", source_id=str(task.id))

    # Close task
    task.status = TaskStatus.COMPLETED
    db_session.commit()

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_task_close"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=443322)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=777, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, msg = await announce_task(task, mock_guild, mock_bot)

    assert ok is True
    mock_msg.edit.assert_called_once()
    edit_args = mock_msg.edit.call_args[1]
    assert edit_args["embed"].title is None
    view = edit_args["view"]
    closed_btn = [b for b in view.children if getattr(b, "custom_id", None) == f"obx:task_card:closed:{task.id}"]
    assert len(closed_btn) == 1
    assert closed_btn[0].disabled is True
    assert "CLOSED" in closed_btn[0].label.upper()


@pytest.mark.asyncio
async def test_task_card_verify_and_details_interactions(db_session):
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.bot.client import OBXTaskBot

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Interactive Card Task",
        description="Verify this interactive task",
        task_type="RETWEET",
        target_url="https://x.com/interactive",
        reward_per_user=20,
        total_reward_pool=200,
        created_by="admin_1",
    )

    bot = OBXTaskBot()

    # 1. Test Verify Completion button click
    mock_int_verify = MagicMock(spec=discord.Interaction)
    mock_int_verify.type = discord.InteractionType.component
    mock_int_verify.data = {"custom_id": f"obx:task_card:verify:{task.id}"}
    mock_int_verify.user = MagicMock(id="user_v1")
    mock_int_verify.response = AsyncMock()

    with patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)):
        await bot.on_interaction(mock_int_verify)

    mock_int_verify.response.send_modal.assert_awaited_once()
    modal = mock_int_verify.response.send_modal.call_args[0][0]
    assert modal.task_id == str(task.id)

    # 2. Test Details button click
    mock_int_details = MagicMock(spec=discord.Interaction)
    mock_int_details.type = discord.InteractionType.component
    mock_int_details.data = {"custom_id": f"obx:task_card:details:{task.id}"}
    mock_int_details.user = MagicMock(id="user_v1")
    mock_int_details.response = AsyncMock()

    with patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)):
        await bot.on_interaction(mock_int_details)

    mock_int_details.response.send_message.assert_awaited_once()
    det_embed = mock_int_details.response.send_message.call_args[1]["embed"]
    assert "Task Details: Interactive Card Task" in det_embed.title
    assert "20 OBX" in det_embed.fields[0].value


def test_task_type_labels_in_announcement_embed():
    from apps.obx_tasks.bot.announcement_service import build_task_announcement_embed
    from packages.shared.enums import TaskStatus

    cases = [
        ("LIKE", "❤️"),
        ("RETWEET", "🔁"),
        ("COMMENT", "💬"),
        ("FOLLOW", "👥"),
        ("CUSTOM_TASK", "📝"),
    ]

    for task_type, expected_emoji in cases:
        mock_task = MagicMock()
        mock_task.id = "test-task-id"
        mock_task.title = f"Test {task_type} Task"
        mock_task.description = "Do the thing"
        mock_task.task_type = task_type
        mock_task.target_url = "https://x.com/test/status/1"
        mock_task.status = TaskStatus.ACTIVE
        mock_task.ends_at = None
        mock_task.distributed_reward = 0
        mock_task.total_reward_pool = 100
        mock_task.reward_per_user = 10
        mock_task.max_approvals = 10
        mock_task.approved_count = 0

        embed = build_task_announcement_embed(mock_task)
        assert embed.title is None
        assert "📝" in embed.description
        assert "10 OBX" in embed.description


@pytest.mark.asyncio
async def test_standalone_url_preview_sent_separately(db_session):
    """First announce_task for a new active X task must send TWO messages:
    1. The embed card (tracked in PublishedMessage)
    2. A plain-text URL message (NOT tracked, for Discord native link preview)
    """
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.bot.announcement_service import announce_task

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_url_test", "tasks", "321", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="URL Preview Test Task",
        description="Like the post",
        task_type="LIKE",
        target_url="https://x.com/test/status/999",
        reward_per_user=5,
        total_reward_pool=50,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_url_test"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=777111)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=321, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, result_msg = await announce_task(task, mock_guild, mock_bot)

    assert ok is True
    # Exactly ONE send: single coherent card with link button (no secondary message)
    assert mock_ch.send.call_count == 1, f"Expected 1 send, got {mock_ch.send.call_count}"

    # Verify embed and link button
    first_call_kwargs = mock_ch.send.call_args_list[0][1]
    assert "embed" in first_call_kwargs
    view = first_call_kwargs["view"]
    link_buttons = [b for b in view.children if getattr(b, "url", None) == "https://x.com/test/status/999"]
    assert len(link_buttons) == 1

    # PublishedMessage tracks this message
    pub = ch_service.get_published_message("guild_url_test", "TASK_ANNOUNCEMENT", source_id=str(task.id))
    assert pub is not None
    assert pub.message_id == "777111"


@pytest.mark.asyncio
async def test_no_url_preview_resent_on_refresh(db_session):
    """When updating an existing task announcement, channel.send must NOT be called again.
    Only msg.edit should be called.
    """
    from apps.obx_tasks.services.task_service import TaskService
    from apps.obx_tasks.bot.announcement_service import announce_task

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("guild_refresh_test", "tasks", "444", "admin")

    task_service = TaskService(db_session)
    task = task_service.create_task(
        title="Refresh URL Test",
        description="Like this post",
        task_type="LIKE",
        target_url="https://x.com/refresh/status/1",
        reward_per_user=5,
        total_reward_pool=50,
        created_by="admin_1",
    )
    # Pre-record as already published
    ch_service.record_published_message("guild_refresh_test", "TASK_ANNOUNCEMENT", "444", "112233", source_id=str(task.id))

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = "guild_refresh_test"
    mock_guild.me = MagicMock()

    mock_msg = MagicMock(id=112233)
    mock_msg.edit = AsyncMock()

    mock_ch = MagicMock(spec=discord.TextChannel, id=444, name="tasks")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_guild.get_channel.return_value = mock_ch

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        ok, result_msg = await announce_task(task, mock_guild, mock_bot)

    assert ok is True
    assert "updated in" in result_msg
    # No new send — only edit
    mock_ch.send.assert_not_called()
    mock_msg.edit.assert_called_once()

