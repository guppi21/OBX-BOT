import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
import discord

from packages.shared.enums import AuctionStatus, AuctionType, ReferenceType
from apps.obx_tasks.services.auction_service import AuctionService
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.auction_management_views import (
    handle_admin_manage_auctions,
    filter_auctions,
    show_auction_detail,
    AdminAuctionBrowserView,
    AdminAuctionDetailView,
    AdminCancelAuctionConfirmView,
)
from apps.obx_tasks.bot.dashboard_views import OBXAdminHubView


def mock_session_scope_for(session):
    from contextlib import contextmanager
    @contextmanager
    def _scope():
        yield session
    return _scope()


def make_mock_interaction(is_done: bool = False):
    inter = MagicMock(spec=discord.Interaction)
    inter.response.is_done.return_value = is_done
    async def _defer(*args, **kwargs):
        inter.response.is_done.return_value = True
    inter.response.defer = AsyncMock(side_effect=_defer)
    inter.response.edit_message = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.user.id = 999
    inter.user.guild_permissions = discord.Permissions(administrator=True)
    return inter


def test_admin_hub_view_contains_manage_auctions_button():
    hub = OBXAdminHubView()
    custom_ids = [btn.custom_id for btn in hub.children if hasattr(btn, "custom_id")]
    assert "obx:admin:manage_auctions" in custom_ids
    manage_btn = next(btn for btn in hub.children if getattr(btn, "custom_id", None) == "obx:admin:manage_auctions")
    assert manage_btn.label == "Manage Auctions"
    assert manage_btn.row == 0


@pytest.mark.asyncio
async def test_handle_admin_manage_auctions_renders_active_auctions(db_session):
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Bidding Test WL",
        reward_title="Pass",
        description="Active auction",
        auction_type=AuctionType.GTD,
        total_slots=2,
        price_or_min_bid=50,
        created_by="admin_1",
    )

    mock_interaction = make_mock_interaction(is_done=False)

    with patch("apps.obx_tasks.bot.auction_management_views.session_scope", lambda: mock_session_scope_for(db_session)):
        with patch("apps.obx_tasks.bot.auction_management_views.is_admin", return_value=True):
            await handle_admin_manage_auctions(mock_interaction)

    mock_interaction.response.defer.assert_called_once_with(ephemeral=True)
    assert mock_interaction.edit_original_response.called
    call_kwargs = mock_interaction.edit_original_response.call_args[1]
    embed = call_kwargs["embed"]
    view = call_kwargs["view"]

    assert "Auction Management" in embed.title
    assert "Bidding Test WL" in embed.description
    assert isinstance(view, AdminAuctionBrowserView)


@pytest.mark.asyncio
async def test_show_auction_detail_displays_metadata_and_actions(db_session):
    service = AuctionService(db_session)
    ws = WalletService(db_session)
    ws.get_or_create_user("bidder_top")
    ws.credit("bidder_top", 1000, "init", "t1")

    auc = service.create_auction(
        title="Detail View WL",
        reward_title="Genesis Pass",
        description="Top bidders win pass",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )
    service.place_or_update_gtd_bid(auc.id, "bidder_top", 250)

    mock_interaction = make_mock_interaction(is_done=True)

    with patch("apps.obx_tasks.bot.auction_management_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await show_auction_detail(mock_interaction, str(auc.id))

    mock_interaction.edit_original_response.assert_called_once()
    call_kwargs = mock_interaction.edit_original_response.call_args[1]
    embed = call_kwargs["embed"]
    view = call_kwargs["view"]

    assert "Detail View WL" in embed.title
    assert any("Genesis Pass" in f.value for f in embed.fields)
    assert isinstance(view, AdminAuctionDetailView)
    btn_labels = [b.label for b in view.children if hasattr(b, "label")]
    assert "Edit Auction" in btn_labels
    assert "Cancel / Delete" in btn_labels
    assert "Settle Now" in btn_labels
    assert "Refresh Card" in btn_labels


@pytest.mark.asyncio
async def test_admin_cancel_auction_confirms_and_refunds_all_bidders_instantly(db_session):
    service = AuctionService(db_session)
    ws = WalletService(db_session)

    # Set up 2 bidders with locked funds
    ws.get_or_create_user("user_cand_1")
    ws.credit("user_cand_1", 1000, "init", "c1")
    ws.get_or_create_user("user_cand_2")
    ws.credit("user_cand_2", 1000, "init", "c2")

    auc = service.create_auction(
        title="Cancelling WL",
        reward_title="Pass",
        description="Will be cancelled",
        auction_type=AuctionType.GTD,
        total_slots=2,
        price_or_min_bid=50,
        created_by="admin_1",
    )
    service.place_or_update_gtd_bid(auc.id, "user_cand_1", 300)
    service.place_or_update_gtd_bid(auc.id, "user_cand_2", 500)

    # Verify funds are locked
    _, w1, _ = ws.get_or_create_user("user_cand_1")
    _, w2, _ = ws.get_or_create_user("user_cand_2")
    assert w1.locked_balance == 300
    assert w2.locked_balance == 500

    mock_guild = MagicMock(spec=discord.Guild, id=12345)
    mock_interaction = make_mock_interaction(is_done=False)
    mock_interaction.guild = mock_guild
    mock_interaction.user.id = 888

    confirm_view = AdminCancelAuctionConfirmView(str(auc.id))

    with patch("apps.obx_tasks.bot.auction_management_views.session_scope", lambda: mock_session_scope_for(db_session)):
        with patch("apps.obx_tasks.bot.auction_management_views.is_admin", return_value=True):
            with patch("apps.obx_tasks.bot.auction_management_views.announce_auction", new_callable=AsyncMock) as mock_ann:
                with patch("apps.obx_tasks.bot.auction_management_views.send_admin_log_event", new_callable=AsyncMock) as mock_log:
                    await confirm_view.confirm_btn.callback(mock_interaction)

    # Verify auction status changed to CANCELLED
    db_session.refresh(auc)
    assert auc.status == AuctionStatus.CANCELLED

    # Verify BOTH bidders received 100% INSTANT REFUND
    db_session.refresh(w1)
    db_session.refresh(w2)
    assert w1.available_balance == 1000
    assert w1.locked_balance == 0
    assert w2.available_balance == 1000
    assert w2.locked_balance == 0

    # Verify announcement card and admin log were triggered
    assert mock_ann.called
    assert mock_log.called

    # Verify response message
    assert mock_interaction.edit_original_response.called
    call_kwargs = mock_interaction.edit_original_response.call_args[1]
    assert "Auction Cancelled & Fully Refunded" in call_kwargs["embed"].title


@pytest.mark.asyncio
async def test_admin_settle_auction_manually_from_detail_view(db_session):
    service = AuctionService(db_session)
    ws = WalletService(db_session)

    ws.get_or_create_user("winner_1")
    ws.credit("winner_1", 1000, "init", "w1")
    ws.get_or_create_user("loser_1")
    ws.credit("loser_1", 1000, "init", "l1")

    auc = service.create_auction(
        title="Manual Settle WL",
        reward_title="Pass",
        description="1 spot GTD",
        auction_type=AuctionType.GTD,
        total_slots=1,
        price_or_min_bid=50,
        created_by="admin_1",
    )
    service.place_or_update_gtd_bid(auc.id, "winner_1", 500)
    service.place_or_update_gtd_bid(auc.id, "loser_1", 200)

    mock_guild = MagicMock(spec=discord.Guild, id=12345)
    mock_interaction = make_mock_interaction(is_done=False)
    mock_interaction.guild = mock_guild
    mock_interaction.user.id = 999

    detail_view = AdminAuctionDetailView(str(auc.id))

    with patch("apps.obx_tasks.bot.auction_management_views.session_scope", lambda: mock_session_scope_for(db_session)):
        with patch("apps.obx_tasks.bot.auction_management_views.is_admin", return_value=True):
            with patch("apps.obx_tasks.bot.auction_management_views.announce_auction_winners", new_callable=AsyncMock):
                with patch("apps.obx_tasks.bot.auction_management_views.announce_auction", new_callable=AsyncMock):
                    with patch("apps.obx_tasks.bot.auction_management_views.send_admin_log_event", new_callable=AsyncMock):
                        await detail_view.settle_btn.callback(mock_interaction)

    db_session.refresh(auc)
    assert auc.status == AuctionStatus.COMPLETED
    assert auc.allocated_slots == 1

    # Winner paid 500
    _, w_win, _ = ws.get_or_create_user("winner_1")
    assert w_win.available_balance == 500
    assert w_win.locked_balance == 0

    # Loser refunded 200 instantly
    _, w_lose, _ = ws.get_or_create_user("loser_1")
    assert w_lose.available_balance == 1000
    assert w_lose.locked_balance == 0


@pytest.mark.asyncio
async def test_raider_outbid_rankings_shows_withdraw_button_and_withdrawing_refunds_wallet(db_session):
    from apps.obx_tasks.bot.client import OBXTaskBot

    service = AuctionService(db_session)
    ws = WalletService(db_session)

    ws.get_or_create_user("outbid_user")
    ws.credit("outbid_user", 1000, "init", "ou1")
    ws.get_or_create_user("top_dog")
    ws.credit("top_dog", 1000, "init", "td1")

    auc = service.create_auction(
        title="Rankings Withdraw Test",
        reward_title="Pass",
        description="1 spot GTD",
        auction_type=AuctionType.GTD,
        total_slots=1,
        price_or_min_bid=50,
        created_by="admin_1",
    )
    # outbid_user bids 100
    service.place_or_update_gtd_bid(auc.id, "outbid_user", 100)
    # top_dog bids 200 (knocks out outbid_user)
    service.place_or_update_gtd_bid(auc.id, "top_dog", 200)

    # Instantiate mock client
    bot = MagicMock(spec=OBXTaskBot)

    # 1. User checks rankings / My Bid
    inter_rankings = make_mock_interaction(is_done=False)
    inter_rankings.user.id = "outbid_user"
    inter_rankings.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)):
        await OBXTaskBot._handle_auc_card_rankings(bot, inter_rankings, str(auc.id))

    assert inter_rankings.followup.send.called
    rank_call_kw = inter_rankings.followup.send.call_args[1]
    rank_embed = rank_call_kw["embed"]
    rank_view = rank_call_kw.get("view")

    assert "Outside Winning Positions" in [f.value for f in rank_embed.fields if f.name == "📍 Your Standing"][0]
    assert rank_view is not None
    assert any("Withdraw Refund" in getattr(btn, "label", "") for btn in rank_view.children)

    # 2. User clicks Withdraw Refund button
    inter_withdraw = make_mock_interaction(is_done=False)
    inter_withdraw.user.id = "outbid_user"
    inter_withdraw.guild = None
    inter_withdraw.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.client.session_scope", lambda: mock_session_scope_for(db_session)):
        await OBXTaskBot._handle_auc_card_withdraw(bot, inter_withdraw, str(auc.id))

    # Verify wallet funds were released back to available instantly
    _, w_outbid, _ = ws.get_or_create_user("outbid_user")
    assert w_outbid.available_balance == 1000
    assert w_outbid.locked_balance == 0

    assert inter_withdraw.followup.send.called
    withdraw_embed = inter_withdraw.followup.send.call_args[1]["embed"]
    assert "Refunded Successfully" in withdraw_embed.title

