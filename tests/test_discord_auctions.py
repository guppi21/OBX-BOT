import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import discord

from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_tasks.bot.auction_views import (
    AuctionCenterView,
    AuctionBrowserView,
    AuctionNotificationCardView,
    build_auction_card_embed,
    build_auction_notification_embed,
    GTDBidModal,
    AdminCreateFCFSModal,
    AdminCreateGTDModal,
    parse_project_and_reward,
    parse_slots_and_price,
)
from apps.obx_tasks.bot.announcement_service import (
    announce_auction,
    announce_auction_winners,
    deploy_or_update_auction_center,
    refresh_all_public_systems,
)
from packages.database.models.auction import Auction
from packages.database.models.channel_config import GuildConfig, PublishedMessage
from packages.shared.enums import AuctionType, AuctionStatus
from packages.shared.utils import parse_duration_or_datetime


@contextmanager
def mock_session_scope_for(db_session):
    yield db_session


def test_auction_center_view_buttons():
    """AuctionCenterView must have 3 auction-scoped buttons and no Home cross-channel button."""
    view = AuctionCenterView()
    assert len(view.children) == 3, f"Expected 3 buttons, got {len(view.children)}: {[b.custom_id for b in view.children]}"

    custom_ids = [b.custom_id for b in view.children]
    assert "obx:auc:active" in custom_ids
    assert "obx:auc:my_activity" in custom_ids
    assert "obx:auc:help" in custom_ids
    # Home cross-channel button must NOT exist in dedicated Auctions channel
    assert "obx:auc:home" not in custom_ids


def test_auction_browser_view_has_no_home_button():
    """AuctionBrowserView must not contain a Home button."""
    from packages.database.models.auction import Auction
    view = AuctionBrowserView(auctions=[])
    home_buttons = [b for b in view.children if getattr(b, "label", "") == "Home"]
    assert len(home_buttons) == 0, "AuctionBrowserView must not have a Home button"


def test_auction_action_success_view_has_no_home_button():
    """AuctionActionSuccessView (bid/claim success) must not contain a Home button."""
    from apps.obx_tasks.bot.auction_views import AuctionActionSuccessView
    view = AuctionActionSuccessView()
    home_buttons = [b for b in view.children if getattr(b, "label", "") == "Home"]
    assert len(home_buttons) == 0, "AuctionActionSuccessView must not have a Home button"


def test_build_auction_notification_embed_gtd_and_fcfs(db_session):
    service = AuctionService(db_session)
    now = datetime.now(timezone.utc)

    # 1. Active GTD Auction
    auc_gtd = service.create_auction(
        title="Berachain VIP",
        reward_title="Tier 1 WL",
        description="Ranked bidding allocation",
        auction_type=AuctionType.GTD,
        total_slots=10,
        price_or_min_bid=100,
        created_by="admin_1",
        ends_at=now + timedelta(hours=2),
    )
    standings = service.get_auction_standings(auc_gtd.id)
    embed_gtd = build_auction_notification_embed(auc_gtd, standings=standings)
    assert embed_gtd.title == "🎟️ WHITELIST AUCTION"
    assert "BERACHAIN VIP" in embed_gtd.description
    assert "10" in embed_gtd.description
    assert "100 OBX" in embed_gtd.description
    assert "<t:" in embed_gtd.description and ":R>" in embed_gtd.description

    # 2. Active FCFS Auction
    auc_fcfs = service.create_auction(
        title="Monad Alpha",
        reward_title="Guaranteed Pass",
        description="Instant WL claim",
        auction_type=AuctionType.FCFS,
        total_slots=25,
        price_or_min_bid=150,
        created_by="admin_1",
        ends_at=now + timedelta(days=1),
    )
    embed_fcfs = build_auction_notification_embed(auc_fcfs)
    assert embed_fcfs.title == "🎟️ WHITELIST SALE"
    assert "MONAD ALPHA" in embed_fcfs.description
    assert "25" in embed_fcfs.description
    assert "150 OBX" in embed_fcfs.description
    assert "<t:" in embed_fcfs.description and ":R>" in embed_fcfs.description


def test_admin_duration_and_exact_utc_parsing():
    now = datetime.now(timezone.utc)

    # Relative durations
    dt_2h = parse_duration_or_datetime("2h")
    assert dt_2h is not None
    assert abs((dt_2h - (now + timedelta(hours=2))).total_seconds()) < 5

    dt_3d = parse_duration_or_datetime("3d")
    assert dt_3d is not None
    assert abs((dt_3d - (now + timedelta(days=3))).total_seconds()) < 5

    # Exact UTC datetime
    dt_exact = parse_duration_or_datetime("2026-10-15 18:00")
    assert dt_exact is not None
    assert dt_exact.year == 2026
    assert dt_exact.month == 10
    assert dt_exact.day == 15
    assert dt_exact.hour == 18
    assert dt_exact.tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_admin_create_gtd_and_fcfs_modals(db_session):
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 123456
    mock_client = MagicMock(spec=discord.Client)

    mock_preview = MagicMock(
        status="SUCCESS",
        author="Berachain VIP",
        description="Top 10 highest bids win.",
        banner_url="https://banner.png",
        avatar_url="https://avatar.png",
        image_url="https://image.png",
    )

    # 1. Admin creates GTD auction via modal
    gtd_modal = AdminCreateGTDModal()
    gtd_modal.slots_and_price._value = "10 / 200"
    gtd_modal.duration._value = "48h"
    gtd_modal.project_x_url._value = "https://x.com/berachain"

    mock_interaction_gtd = MagicMock(spec=discord.Interaction)
    mock_interaction_gtd.user = MagicMock(id="admin_user_1")
    mock_interaction_gtd.guild = mock_guild
    mock_interaction_gtd.client = mock_client
    mock_interaction_gtd.response = AsyncMock()
    mock_interaction_gtd.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", AsyncMock(return_value=mock_preview)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "OK"))), \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_auction_center", AsyncMock(return_value=(True, "OK"))):
        await gtd_modal.on_submit(mock_interaction_gtd)
        confirm_view = mock_interaction_gtd.followup.send.call_args[1]["view"]
        await confirm_view.children[0].callback(mock_interaction_gtd)

    service = AuctionService(db_session)
    auctions, total = service.list_auctions()
    gtd_created = [a for a in auctions if a.title == "Berachain VIP"][0]
    assert gtd_created.reward_title == "WL"
    assert gtd_created.total_slots == 10
    assert gtd_created.price_or_min_bid == 200
    assert gtd_created.auction_type == AuctionType.GTD
    assert gtd_created.ends_at is not None
    assert gtd_created.project_x_url == "https://x.com/berachain"

    # 2. Admin creates FCFS sale via modal
    fcfs_modal = AdminCreateFCFSModal()
    fcfs_modal.slots_and_price._value = "50 / 150"
    fcfs_modal.duration._value = "2026-11-01 12:00"
    fcfs_modal.project_x_url._value = "https://x.com/monad"

    mock_preview_monad = MagicMock(
        status="SUCCESS",
        author="Monad",
        description="Instant Alpha Pass",
        banner_url="https://banner.png",
        avatar_url="https://avatar.png",
        image_url="https://image.png",
    )

    mock_interaction_fcfs = MagicMock(spec=discord.Interaction)
    mock_interaction_fcfs.user = MagicMock(id="admin_user_1")
    mock_interaction_fcfs.guild = mock_guild
    mock_interaction_fcfs.client = mock_client
    mock_interaction_fcfs.response = AsyncMock()
    mock_interaction_fcfs.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.services.url_preview_service.UrlPreviewService.fetch_preview", AsyncMock(return_value=mock_preview_monad)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "OK"))), \
         patch("apps.obx_tasks.bot.announcement_service.deploy_or_update_auction_center", AsyncMock(return_value=(True, "OK"))):
        await fcfs_modal.on_submit(mock_interaction_fcfs)
        confirm_view = mock_interaction_fcfs.followup.send.call_args[1]["view"]
        await confirm_view.children[0].callback(mock_interaction_fcfs)

    auctions, total = service.list_auctions()
    fcfs_created = [a for a in auctions if a.title == "Monad"][0]
    assert fcfs_created.reward_title == "WL"
    assert fcfs_created.total_slots == 50
    assert fcfs_created.price_or_min_bid == 150
    assert fcfs_created.auction_type == AuctionType.GTD
    assert fcfs_created.ends_at.year == 2026


@pytest.mark.asyncio
async def test_announce_auction_publishes_and_updates_card_in_place(db_session):
    ch_service = ChannelService(db_session)
    config = ch_service.get_or_create_guild_config("1001")
    config.auctions_channel_id = "2002"
    db_session.commit()

    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="Live Card Project",
        reward_title="Genesis Pass",
        description="Desc",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 1001
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 2002
    mock_channel.name = "2-🔨・auctions"
    mock_channel.mention = "<#2002>"
    mock_channel.permissions_for.return_value.view_channel = True
    mock_channel.permissions_for.return_value.send_messages = True
    mock_channel.permissions_for.return_value.embed_links = True
    mock_guild.get_channel.return_value = mock_channel

    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 99901
    mock_msg.edit = AsyncMock()
    mock_channel.send = AsyncMock(return_value=mock_msg)
    mock_channel.fetch_message = AsyncMock(return_value=mock_msg)

    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # 1. First announcement posts new message
        ok, msg = await announce_auction(auc, mock_guild, mock_bot)
        assert ok is True
        mock_channel.send.assert_awaited_once()

        # Check recorded published message
        pub_rec = ch_service.get_published_message("1001", feature_type="AUCTION_ANNOUNCEMENT", source_id=str(auc.id))
        assert pub_rec is not None
        assert pub_rec.message_id == "99901"

        # 2. Second announcement updates existing message in place (zero duplicate spam!)
        ok2, msg2 = await announce_auction(auc, mock_guild, mock_bot)
        assert ok2 is True
        mock_msg.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_gtd_bid_modal_immediate_defer_and_single_lock(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("modal_bidder_1")
    ws.credit("modal_bidder_1", 1000, "test", "mb_init")

    auc = service.create_auction(
        title="Deferred GTD",
        reward_title="Pass",
        description="Bidding test",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    modal = GTDBidModal(auction_id=str(auc.id), auction_title=auc.title, min_bid=100)
    modal.bid_amount_input._value = "400"

    mock_interaction = MagicMock(spec=discord.Interaction)
    mock_interaction.user = MagicMock(id="modal_bidder_1")
    mock_interaction.response = AsyncMock()
    mock_interaction.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_interaction)

    # Defer must have been called immediately
    mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
    mock_interaction.followup.send.assert_called_once()

    # Wallet check
    _, w, _ = ws.get_or_create_user("modal_bidder_1")
    assert w.available_balance == 600
    assert w.locked_balance == 400
    assert w.total_balance == 1000


@pytest.mark.asyncio
async def test_gtd_bid_retry_after_discord_timeout_is_idempotent(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("timeout_bidder")
    ws.credit("timeout_bidder", 1000, "test", "tb_init")

    auc = service.create_auction(
        title="Retry GTD",
        reward_title="Pass",
        description="Bidding retry test",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    modal = GTDBidModal(auction_id=str(auc.id), auction_title=auc.title, min_bid=100)
    modal.bid_amount_input._value = "500"

    mock_interaction_1 = MagicMock(spec=discord.Interaction)
    mock_interaction_1.user = MagicMock(id="timeout_bidder")
    mock_interaction_1.response = AsyncMock()
    # Simulate Discord followup error
    mock_interaction_1.followup = AsyncMock()
    mock_interaction_1.followup.send.side_effect = discord.HTTPException(MagicMock(status=504), "Gateway Timeout")

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_interaction_1)

    # Verify wallet has 500 locked
    _, w, _ = ws.get_or_create_user("timeout_bidder")
    assert w.available_balance == 500
    assert w.locked_balance == 500

    # User retries submitting the exact same bid of 500
    mock_interaction_2 = MagicMock(spec=discord.Interaction)
    mock_interaction_2.user = MagicMock(id="timeout_bidder")
    mock_interaction_2.response = AsyncMock()
    mock_interaction_2.followup = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_interaction_2)

    # Wallet must STILL have exactly 500 locked (zero double locking!)
    db_session.refresh(w)
    assert w.available_balance == 500
    assert w.locked_balance == 500
    assert w.total_balance == 1000


@pytest.mark.asyncio
async def test_auction_auto_expiry_settlement_and_winner_routing(db_session):
    ws = WalletService(db_session)
    auc_service = AuctionService(db_session)
    ch_service = ChannelService(db_session)

    config = ch_service.get_or_create_guild_config("1001")
    config.auctions_channel_id = "2002"
    config.winners_channel_id = "4004"
    db_session.commit()

    # Seed 3 bidders
    for uid, amt in [("u1", 500), ("u2", 300), ("u3", 200)]:
        ws.get_or_create_user(uid)
        ws.credit(uid, 1000, "test", f"seed_{uid}")

    now = datetime.now(timezone.utc)
    auc = auc_service.create_auction(
        title="Settlement Auction",
        reward_title="Top 2 Pass",
        description="Auto settlement test",
        auction_type=AuctionType.GTD,
        total_slots=2,
        price_or_min_bid=100,
        created_by="admin_1",
        ends_at=now + timedelta(hours=1),
    )

    auc_service.place_or_update_gtd_bid(auc.id, "u1", 500)
    auc_service.place_or_update_gtd_bid(auc.id, "u2", 300)
    auc_service.place_or_update_gtd_bid(auc.id, "u3", 200)

    # Time passes: auction ends_at is now in the past
    auc.ends_at = now - timedelta(minutes=5)
    db_session.commit()

    # Auto expire and settle
    settled_list = auc_service.auto_expire_and_settle_auctions()
    assert len(settled_list) == 1
    settled_auc, winners, total_bids = settled_list[0]
    assert settled_auc.status == AuctionStatus.COMPLETED
    assert len(winners) == 2
    assert winners[0].discord_user_id == "u1"
    assert winners[1].discord_user_id == "u2"

    # Verify wallet accounting post settlement
    _, w1, _ = ws.get_or_create_user("u1")
    _, w2, _ = ws.get_or_create_user("u2")
    _, w3, _ = ws.get_or_create_user("u3")

    # u1 paid 500
    assert w1.available_balance == 500
    assert w1.locked_balance == 0
    # u2 paid 300
    assert w2.available_balance == 700
    assert w2.locked_balance == 0
    # u3 lost -> 200 unlocked and refunded!
    assert w3.available_balance == 1000
    assert w3.locked_balance == 0

    # Test winner announcement routing to Winners channel
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 1001
    mock_win_channel = MagicMock(spec=discord.TextChannel)
    mock_win_channel.id = 4004
    mock_win_channel.permissions_for.return_value.view_channel = True
    mock_win_channel.permissions_for.return_value.send_messages = True
    mock_win_channel.permissions_for.return_value.embed_links = True
    mock_guild.get_channel.return_value = mock_win_channel

    mock_msg = MagicMock(spec=discord.Message)
    mock_msg.id = 99902
    mock_win_channel.send = AsyncMock(return_value=mock_msg)
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)), \
         patch("apps.obx_tasks.bot.announcement_service.announce_auction", AsyncMock(return_value=(True, "OK"))):
        ok, res = await announce_auction_winners(settled_auc, winners, total_bids, mock_guild, mock_bot)
        assert ok is True
        mock_win_channel.send.assert_awaited_once()
        embed = mock_win_channel.send.call_args[1]["embed"]
        assert "AUCTION RESULTS" in embed.title
        assert "Settlement Auction" in embed.description


@pytest.mark.asyncio
async def test_fcfs_claim_success_message_hides_transaction_id(db_session):
    """Verify FCFS claim confirmation hides internal transaction IDs from users."""
    from apps.obx_core.services.wallet_service import WalletService
    from apps.obx_tasks.bot.client import OBXTaskBot

    ws = WalletService(db_session)
    ws.get_or_create_user("u_claim_test")
    ws.credit("u_claim_test", 100, "test", "seed_claim")

    auc_service = AuctionService(db_session)
    auc = auc_service.create_auction(
        title="FCFS Privacy Test",
        reward_title="VIP Whitelist Pass",
        description="Instant whitelist pass",
        auction_type=AuctionType.FCFS,
        total_slots=2,
        price_or_min_bid=25,
        created_by="admin_test",
    )

    mock_bot = MagicMock(spec=OBXTaskBot)
    mock_bot.is_closed.return_value = False
    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "u_claim_test"
    mock_intr.guild = None
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)):
        await OBXTaskBot._handle_auc_card_claim(mock_bot, mock_intr, str(auc.id))

    mock_intr.followup.send.assert_awaited_once()
    kwargs = mock_intr.followup.send.call_args[1]
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]

    # Must contain clean friendly notification
    assert embed.title == "🎉 WHITELIST CLAIMED!"
    assert "VIP Whitelist Pass" in embed.description
    assert "25 OBX" in embed.description
    assert "REMAINING SPOTS" in embed.description
    assert "Your spot is secured. Good luck." in embed.description

    # Must NEVER expose internal transaction IDs to users
    assert "Transaction ID" not in embed.description
    assert "Ledger Transaction ID" not in embed.description
    assert "obx_transaction_id" not in embed.description


@pytest.mark.asyncio
async def test_auction_type_immutability_and_button_derivation(db_session):
    """Ensure an auction's type is strictly preserved and correctly derives button actions."""
    from apps.obx_tasks.services.channel_service import ChannelService
    from apps.obx_tasks.bot.announcement_service import announce_auction

    ch_service = ChannelService(db_session)
    ch_service.update_guild_channel("g_auc_type_test", "auctions", "9901", "admin")

    auc_service = AuctionService(db_session)
    # 1. Create GTD Ranked Auction
    gtd_auc = auc_service.create_auction(
        title="GTD Auction Alpha",
        reward_title="Alpha Whitelist",
        description="Ranked bidding allocation",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=50,
        created_by="admin_test",
    )

    # 2. Create FCFS Sale
    fcfs_auc = auc_service.create_auction(
        title="FCFS Sale Beta",
        reward_title="Beta Whitelist",
        description="Instant claim allocation",
        auction_type=AuctionType.FCFS,
        total_slots=3,
        price_or_min_bid=10,
        created_by="admin_test",
    )

    mock_guild = MagicMock(spec=discord.Guild, id="g_auc_type_test")
    mock_msg_gtd = MagicMock(id=1111)
    mock_msg_fcfs = MagicMock(id=2222)
    mock_ch = MagicMock(spec=discord.TextChannel, id=9901, name="auctions")
    mock_ch.permissions_for.return_value = MagicMock(view_channel=True, send_messages=True, embed_links=True)
    mock_ch.send = AsyncMock(side_effect=[mock_msg_gtd, mock_msg_fcfs])
    mock_guild.get_channel.return_value = mock_ch
    mock_bot = MagicMock(spec=discord.Client)

    with patch("apps.obx_tasks.bot.announcement_service.session_scope", lambda: mock_session_scope_for(db_session)):
        # Announce GTD
        ok_gtd, _ = await announce_auction(gtd_auc, mock_guild, mock_bot)
        assert ok_gtd is True
        gtd_view = mock_ch.send.call_args_list[0][1]["view"]
        gtd_labels = [b.label for b in gtd_view.children if hasattr(b, "label")]
        assert "BID" in gtd_labels
        assert "CLAIM SPOT" not in gtd_labels

        # Announce FCFS
        ok_fcfs, _ = await announce_auction(fcfs_auc, mock_guild, mock_bot)
        assert ok_fcfs is True
        fcfs_view = mock_ch.send.call_args_list[1][1]["view"]
        fcfs_labels = [b.label for b in fcfs_view.children if hasattr(b, "label")]
        assert "CLAIM SPOT" in fcfs_labels
        assert "BID" not in fcfs_labels

    # Verify database immutability: auction types were NOT changed
    refreshed_gtd = auc_service.get_auction(gtd_auc.id)
    refreshed_fcfs = auc_service.get_auction(fcfs_auc.id)
    assert refreshed_gtd.auction_type == AuctionType.GTD
    assert refreshed_fcfs.auction_type == AuctionType.FCFS
