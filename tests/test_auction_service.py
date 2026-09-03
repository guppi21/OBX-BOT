import pytest
from datetime import datetime, timedelta, timezone
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_tasks.services.auction_service import AuctionService, AuctionError
from packages.database.models.auction import Auction, AuctionBid, AuctionClaim
from packages.shared.enums import AuctionType, AuctionStatus, ReferenceType


def test_create_fcfs_and_gtd_auctions(db_session):
    service = AuctionService(db_session)

    # 1. FCFS
    fcfs = service.create_auction(
        title="Monad Testnet",
        reward_title="Early Adopter WL",
        description="Fast claim whitelist",
        auction_type=AuctionType.FCFS,
        total_slots=25,
        price_or_min_bid=100,
        created_by="admin_test_1",
    )
    assert fcfs.id is not None
    assert fcfs.auction_type == AuctionType.FCFS
    assert fcfs.total_slots == 25
    assert fcfs.remaining_slots == 25
    assert fcfs.price_or_min_bid == 100
    assert fcfs.status == AuctionStatus.ACTIVE

    # 2. GTD
    gtd = service.create_auction(
        title="Berachain VIP",
        reward_title="Guaranteed WL Pass",
        description="Top 5 bidders win",
        auction_type=AuctionType.GTD,
        total_slots=5,
        price_or_min_bid=50,
        created_by="admin_test_1",
    )
    assert gtd.id is not None
    assert gtd.auction_type == AuctionType.GTD
    assert gtd.total_slots == 5
    assert gtd.price_or_min_bid == 50


def test_fcfs_claim_success_and_obx_debit(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("fcfs_user_1")
    ws.credit(discord_user_id="fcfs_user_1", amount=500, reference_type="test", idempotency_key="fcfs_c1")

    auc = service.create_auction(
        title="Project X",
        reward_title="VIP WL",
        description="Claim now",
        auction_type=AuctionType.FCFS,
        total_slots=10,
        price_or_min_bid=150,
        created_by="admin_1",
    )

    claim = service.claim_fcfs_slot(auc.id, discord_user_id="fcfs_user_1")
    assert claim.price_paid == 150
    assert claim.discord_user_id == "fcfs_user_1"

    # Wallet check
    _, w, _ = ws.get_or_create_user("fcfs_user_1")
    assert w.available_balance == 350
    assert w.total_balance == 350

    # Auction state check
    db_session.refresh(auc)
    assert auc.allocated_slots == 1
    assert auc.remaining_slots == 9


def test_fcfs_insufficient_balance_rejected(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("poor_user")
    ws.credit(discord_user_id="poor_user", amount=50, reference_type="test", idempotency_key="p_1")

    auc = service.create_auction(
        title="Expensive WL",
        reward_title="High Tier Pass",
        description="Costs 200 OBX",
        auction_type=AuctionType.FCFS,
        total_slots=5,
        price_or_min_bid=200,
        created_by="admin_1",
    )

    with pytest.raises(AuctionError, match="Insufficient available funds"):
        service.claim_fcfs_slot(auc.id, discord_user_id="poor_user")


def test_fcfs_duplicate_claim_rejected(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("dup_user")
    ws.credit(discord_user_id="dup_user", amount=1000, reference_type="test", idempotency_key="d_1")

    auc = service.create_auction(
        title="One Per Person",
        reward_title="Single Pass",
        description="Only 1 claim allowed",
        auction_type=AuctionType.FCFS,
        total_slots=5,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    service.claim_fcfs_slot(auc.id, discord_user_id="dup_user")

    with pytest.raises(AuctionError, match="already claimed"):
        service.claim_fcfs_slot(auc.id, discord_user_id="dup_user")


def test_fcfs_slot_exhaustion_marks_completed(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("user_a")
    ws.credit("user_a", 500, "test", "ua_1")
    ws.get_or_create_user("user_b")
    ws.credit("user_b", 500, "test", "ub_1")
    ws.get_or_create_user("user_c")
    ws.credit("user_c", 500, "test", "uc_1")

    auc = service.create_auction(
        title="2 Slots Only",
        reward_title="Rare WL",
        description="Only 2 available",
        auction_type=AuctionType.FCFS,
        total_slots=2,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    service.claim_fcfs_slot(auc.id, "user_a")
    service.claim_fcfs_slot(auc.id, "user_b")

    db_session.refresh(auc)
    assert auc.status == AuctionStatus.COMPLETED
    assert auc.remaining_slots == 0

    with pytest.raises(AuctionError, match="All whitelist slots have already been claimed|not active"):
        service.claim_fcfs_slot(auc.id, "user_c")


def test_gtd_first_bid_locks_available_funds(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("bidder_1")
    ws.credit("bidder_1", 1000, "test", "b1_1")

    auc = service.create_auction(
        title="GTD Auction",
        reward_title="Top Pass",
        description="Min 100 OBX",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    bid = service.place_or_update_gtd_bid(auc.id, "bidder_1", bid_amount=300)
    assert bid.bid_amount == 300

    _, w, _ = ws.get_or_create_user("bidder_1")
    assert w.available_balance == 700
    assert w.locked_balance == 300
    assert w.total_balance == 1000


def test_gtd_bid_increase_adjusts_locked_funds_without_double_locking(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("bidder_inc")
    ws.credit("bidder_inc", 1000, "test", "bi_1")

    auc = service.create_auction(
        title="GTD Auction",
        reward_title="Top Pass",
        description="Min 100 OBX",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    # 1. First bid: 200
    service.place_or_update_gtd_bid(auc.id, "bidder_inc", bid_amount=200)
    _, w, _ = ws.get_or_create_user("bidder_inc")
    assert w.available_balance == 800
    assert w.locked_balance == 200

    # 2. Increase bid: 500 (delta = 300)
    bid = service.place_or_update_gtd_bid(auc.id, "bidder_inc", bid_amount=500)
    assert bid.bid_amount == 500

    db_session.refresh(w)
    assert w.available_balance == 500
    assert w.locked_balance == 500
    assert w.total_balance == 1000


def test_gtd_bid_below_min_bid_rejected(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("bidder_low")
    ws.credit("bidder_low", 1000, "test", "bl_1")

    auc = service.create_auction(
        title="GTD Auction",
        reward_title="Top Pass",
        description="Min 100 OBX",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    with pytest.raises(AuctionError, match="at least the minimum bid"):
        service.place_or_update_gtd_bid(auc.id, "bidder_low", bid_amount=50)


def test_gtd_bid_decrease_unlocks_funds_back_to_available(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("dec_bidder")
    ws.credit("dec_bidder", 1000, "test", "dec_init")

    auc = service.create_auction(
        title="Decrease Test",
        reward_title="Pass",
        description="Testing bid decrease",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    # Place bid: 800 OBX
    service.place_or_update_gtd_bid(auc.id, "dec_bidder", 800)
    _, w, _ = ws.get_or_create_user("dec_bidder")
    assert w.available_balance == 200
    assert w.locked_balance == 800

    # Lower bid to 500 OBX -> 300 unlocked
    bid = service.place_or_update_gtd_bid(auc.id, "dec_bidder", 500)
    assert bid.bid_amount == 500

    db_session.refresh(w)
    assert w.available_balance == 500
    assert w.locked_balance == 500
    assert w.total_balance == 1000


def test_auction_standings_and_winning_cutoff_calculation(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    for i in range(1, 6):
        uid = f"user_stand_{i}"
        ws.get_or_create_user(uid)
        ws.credit(uid, 5000, "test", f"s_init_{i}")

    # 3 slots available
    auc = service.create_auction(
        title="Cutoff Auction",
        reward_title="3 Spots",
        description="Top 3 win",
        auction_type=AuctionType.GTD,
        total_slots=3,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    # Bids: user 1=1000, user 2=2000, user 3=3000, user 4=4000, user 5=500
    service.place_or_update_gtd_bid(auc.id, "user_stand_1", 1000)
    service.place_or_update_gtd_bid(auc.id, "user_stand_2", 2000)
    service.place_or_update_gtd_bid(auc.id, "user_stand_3", 3000)
    service.place_or_update_gtd_bid(auc.id, "user_stand_4", 4000)
    service.place_or_update_gtd_bid(auc.id, "user_stand_5", 500)

    # Top 3 are: user 4 (4000), user 3 (3000), user 2 (2000) -> Cutoff = 2000
    # user_stand_4: rank #1, is_winning = True
    standings_4 = service.get_auction_standings(auc.id, discord_user_id="user_stand_4")
    assert standings_4["user_rank"] == 1
    assert standings_4["is_winning"] is True
    assert standings_4["winning_cutoff"] == 2000

    # user_stand_1: rank #4 (1000 OBX), is_winning = False
    standings_1 = service.get_auction_standings(auc.id, discord_user_id="user_stand_1")
    assert standings_1["user_rank"] == 4
    assert standings_1["is_winning"] is False


def test_gtd_settlement_pay_as_bid_winners_and_loser_refunds(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    # Setup 3 users with 1000 OBX each
    for uid in ["user_win_a", "user_win_b", "user_lose_c"]:
        ws.get_or_create_user(uid)
        ws.credit(uid, 1000, "test", f"init_{uid}")

    # GTD auction with 2 guaranteed winner spots
    auc = service.create_auction(
        title="GTD Big Drop",
        reward_title="Gold Tier WL",
        description="Top 2 win",
        auction_type=AuctionType.GTD,
        total_slots=2,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    # Bids: User A = 500, User B = 400, User C = 300
    service.place_or_update_gtd_bid(auc.id, "user_win_a", 500)
    service.place_or_update_gtd_bid(auc.id, "user_win_b", 400)
    service.place_or_update_gtd_bid(auc.id, "user_lose_c", 300)

    # Finalize auction
    _, winners, losers = service.settle_and_finalize_auction(auc.id, finalized_by="admin_1")
    assert len(winners) == 2
    assert len(losers) == 1

    # Winner A: Pays 500 -> total balance = 500, locked = 0, available = 500
    _, wa, _ = ws.get_or_create_user("user_win_a")
    assert wa.available_balance == 500
    assert wa.locked_balance == 0
    assert wa.total_balance == 500

    # Winner B: Pays 400 -> total balance = 600, locked = 0, available = 600
    _, wb, _ = ws.get_or_create_user("user_win_b")
    assert wb.available_balance == 600
    assert wb.locked_balance == 0
    assert wb.total_balance == 600

    # Loser C: 300 locked is fully released -> available = 1000, locked = 0, total = 1000
    _, wc, _ = ws.get_or_create_user("user_lose_c")
    assert wc.available_balance == 1000
    assert wc.locked_balance == 0
    assert wc.total_balance == 1000

    # Claims check
    claims = db_session.query(AuctionClaim).filter_by(auction_id=auc.id).all()
    assert len(claims) == 2
    claimed_users = {c.discord_user_id for c in claims}
    assert "user_win_a" in claimed_users
    assert "user_win_b" in claimed_users


def test_auction_cancellation_releases_all_locked_gtd_bids(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    ws.get_or_create_user("cancel_bidder_1")
    ws.credit("cancel_bidder_1", 1000, "test", "cb1")
    ws.get_or_create_user("cancel_bidder_2")
    ws.credit("cancel_bidder_2", 1000, "test", "cb2")

    auc = service.create_auction(
        title="To be cancelled",
        reward_title="Pass",
        description="Will be cancelled",
        auction_type=AuctionType.GTD,
        total_slots=2,
        price_or_min_bid=100,
        created_by="admin_1",
    )

    service.place_or_update_gtd_bid(auc.id, "cancel_bidder_1", 400)
    service.place_or_update_gtd_bid(auc.id, "cancel_bidder_2", 600)

    # Cancel auction
    service.cancel_auction(auc.id, cancelled_by="admin_1")

    # Both users must have locked balance returned to available
    _, w1, _ = ws.get_or_create_user("cancel_bidder_1")
    _, w2, _ = ws.get_or_create_user("cancel_bidder_2")

    assert w1.available_balance == 1000
    assert w1.locked_balance == 0
    assert w2.available_balance == 1000
    assert w2.locked_balance == 0


def test_grant_custom_reward_creates_ledger_record(db_session):
    ws = WalletService(db_session)
    service = AuctionService(db_session)

    entry = service.grant_custom_reward(
        admin_discord_id="admin_granter",
        target_discord_id="reward_winner_1",
        amount=750,
        reason="Winner of community art contest",
    )

    assert entry.amount == 750
    assert entry.reference_type == ReferenceType.ADMIN

    _, w, _ = ws.get_or_create_user("reward_winner_1")
    assert w.available_balance == 750
    assert w.total_balance == 750
