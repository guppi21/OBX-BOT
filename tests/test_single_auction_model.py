import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import discord
from packages.database.session import session_scope
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.shared.enums import AuctionType, AuctionStatus, ReferenceType
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from apps.obx_tasks.services.channel_service import ChannelService
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.bot.auction_views import (
    build_auction_notification_embed,
    AuctionNotificationCardView,
    AdminCreateAuctionModal,
    GTDBidModal,
)


def mock_session_scope_for(session):
    class _Scope:
        def __enter__(self):
            return session
        def __exit__(self, exc_type, exc_val, exc_tb):
            pass
    return _Scope()


def test_one_spot_creates_ranked_auction(db_session):
    """1 spot creates a ranked bidding auction."""
    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Single Pass Drop",
        reward_title="Genesis 1/1 Pass",
        description="Exclusive 1-of-1 allocation",
        total_slots=1,
        price_or_min_bid=50,
        created_by="admin_1",
    )
    assert auc.total_slots == 1
    assert auc.auction_type == AuctionType.GTD


def test_one_spot_and_multi_spots_use_place_bid_never_claim_spot():
    """1 spot and multiple spots both use Place Bid, never Claim Spot."""
    now = datetime.now(timezone.utc)

    # 1 Spot
    auc_1 = MagicMock(spec=Auction)
    auc_1.id = uuid.uuid4()
    auc_1.title = "1-Spot Alpha"
    auc_1.reward_title = "Alpha Key"
    auc_1.description = "1 of 1 key"
    auc_1.total_slots = 1
    auc_1.price_or_min_bid = 10
    auc_1.status = AuctionStatus.ACTIVE
    auc_1.ends_at = now + timedelta(minutes=2)
    auc_1.image_url = None
    auc_1.external_url = None
    auc_1.preview_x_handle = None
    auc_1.preview_x_display_name = None
    auc_1.preview_x_avatar_url = None
    auc_1.preview_x_banner_url = None

    embed_1 = build_auction_notification_embed(auc_1)
    view_1 = AuctionNotificationCardView(auction_id=str(auc_1.id), is_active=True)
    labels_1 = [b.label for b in view_1.children if hasattr(b, "label")]

    assert "WINNERS" in embed_1.description
    assert "MIN BID" in embed_1.description
    assert "10 OBX" in embed_1.description
    assert "BID" in labels_1
    assert "CLAIM SPOT" not in labels_1

    # Forbidden public FCFS strings
    for forbidden in ["FCFS SALE", "LIVE FCFS SALE", "FIXED PRICE", "SOLD OUT", "WHITELIST CLAIMED"]:
        assert forbidden not in embed_1.title
        assert forbidden not in embed_1.description

    # 5 Spots
    auc_5 = MagicMock(spec=Auction)
    auc_5.id = uuid.uuid4()
    auc_5.title = "5-Spot Beta"
    auc_5.reward_title = "Beta Pass"
    auc_5.description = "Top 5 win"
    auc_5.total_slots = 5
    auc_5.price_or_min_bid = 25
    auc_5.status = AuctionStatus.ACTIVE
    auc_5.ends_at = now + timedelta(minutes=10)
    auc_5.image_url = None
    auc_5.external_url = None
    auc_5.preview_x_handle = None
    auc_5.preview_x_display_name = None
    auc_5.preview_x_avatar_url = None
    auc_5.preview_x_banner_url = None

    embed_5 = build_auction_notification_embed(auc_5)
    view_5 = AuctionNotificationCardView(auction_id=str(auc_5.id), is_active=True)
    labels_5 = [b.label for b in view_5.children if hasattr(b, "label")]

    assert "WINNERS" in embed_5.description
    assert "MIN BID" in embed_5.description
    assert "25 OBX" in embed_5.description
    assert "BID" in labels_5
    assert "CLAIM SPOT" not in labels_5


@pytest.mark.asyncio
async def test_no_auction_is_immediately_won_after_placing_bid(db_session):
    """Placing a bid must lock OBX safely but NEVER immediately declare a win or claim."""
    ws = WalletService(db_session)
    ws.get_or_create_user("bidder_one")
    ws.credit("bidder_one", 200, ReferenceType.ADMIN, "seed_bidder")

    service = AuctionService(db_session)
    auc = service.create_auction(
        title="1-Spot Test",
        reward_title="Alpha WL",
        description="Top bidder takes all",
        total_slots=1,
        price_or_min_bid=10,
        ends_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        created_by="admin",
    )

    modal = GTDBidModal(
        auction_id=str(auc.id),
        auction_title="1-Spot Test — Alpha WL",
        min_bid=10,
    )
    modal.bid_amount_input._value = "50"

    mock_intr = MagicMock(spec=discord.Interaction)
    mock_intr.user.id = "bidder_one"
    mock_intr.response = MagicMock()
    mock_intr.response.defer = AsyncMock()
    mock_intr.followup = MagicMock()
    mock_intr.followup.send = AsyncMock()

    with patch("apps.obx_tasks.bot.auction_views.session_scope", lambda: mock_session_scope_for(db_session)):
        await modal.on_submit(mock_intr)

    mock_intr.followup.send.assert_awaited_once()
    kwargs = mock_intr.followup.send.call_args[1]
    embed = kwargs["embed"]

    # Verify required bid success format
    assert embed.title == "💎 BID PLACED"
    assert "Your bid has been successfully secured." in embed.description
    assert "50 OBX" in embed.description
    assert "WHITELIST SPOTS" in embed.description
    assert "YOUR CURRENT POSITION" in embed.description
    assert "Your position can change as new bids arrive." in embed.description

    # Forbidden premature win indicators
    assert "WHITELIST CLAIMED" not in embed.title
    assert "WHITELIST CLAIMED" not in embed.description
    assert "You successfully secured the whitelist" not in embed.description
    assert "Remaining spots" not in embed.description

    # Verify no claim exists yet in DB
    claims = db_session.query(AuctionClaim).filter_by(auction_id=auc.id).all()
    assert len(claims) == 0

    # Verify funds are locked in vault, not spent
    _, w, _ = ws.get_or_create_user("bidder_one")
    assert w.available_balance == 150
    assert w.locked_balance == 50


def test_settlement_top_n_win_n_spots_and_losers_refunded(db_session):
    """Top N bidders win N spots; losers refunded; ledger balanced."""
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    # Setup 4 bidders
    bidders = [("u_1", 300), ("u_2", 200), ("u_3", 100), ("u_4", 50)]
    for uid, amt in bidders:
        ws.get_or_create_user(uid)
        ws.credit(uid, 500, ReferenceType.ADMIN, f"init_{uid}")

    now = datetime.now(timezone.utc)
    # Auction with 2 spots
    auc = service.create_auction(
        title="Ranked 2-Spot Auction",
        reward_title="Whitelist Pass",
        description="Top 2 win",
        total_slots=2,
        price_or_min_bid=10,
        ends_at=now + timedelta(seconds=1),
        created_by="admin",
    )

    for uid, amt in bidders:
        service.place_or_update_gtd_bid(auc.id, uid, amt)

    # Finalize & settle
    final_auc, winners, losers = service.settle_and_finalize_auction(auc.id)

    # Winners: top 2 (u_1 with 300, u_2 with 200)
    assert len(winners) == 2
    assert winners[0].discord_user_id == "u_1"
    assert winners[0].bid_amount == 300
    assert winners[1].discord_user_id == "u_2"
    assert winners[1].bid_amount == 200

    # Losers: remaining (u_3 with 100, u_4 with 50)
    assert len(losers) == 2
    assert losers[0].discord_user_id == "u_3"
    assert losers[1].discord_user_id == "u_4"

    # Verify wallet balances after settlement
    _, w1, _ = ws.get_or_create_user("u_1")
    _, w2, _ = ws.get_or_create_user("u_2")
    _, w3, _ = ws.get_or_create_user("u_3")
    _, w4, _ = ws.get_or_create_user("u_4")

    # u_1 paid 300 (500 - 300 = 200 available, 0 locked)
    assert w1.available_balance == 200
    assert w1.locked_balance == 0

    # u_2 paid 200 (500 - 200 = 300 available, 0 locked)
    assert w2.available_balance == 300
    assert w2.locked_balance == 0

    # u_3 lost: 100 locked was refunded (500 available, 0 locked)
    assert w3.available_balance == 500
    assert w3.locked_balance == 0

    # u_4 lost: 50 locked was refunded (500 available, 0 locked)
    assert w4.available_balance == 500
    assert w4.locked_balance == 0


def test_minimum_bid_is_enforced(db_session):
    """Bidding below minimum bid is rejected."""
    ws = WalletService(db_session)
    ws.get_or_create_user("u_min")
    ws.credit("u_min", 100, ReferenceType.ADMIN, "init_min")

    service = AuctionService(db_session)
    auc = service.create_auction(
        title="Min Bid Auction",
        reward_title="Key",
        description="Rules",
        total_slots=1,
        price_or_min_bid=50,
        created_by="admin",
    )

    with pytest.raises(AuctionError, match="must be at least"):
        service.place_or_update_gtd_bid(auc.id, "u_min", 20)
