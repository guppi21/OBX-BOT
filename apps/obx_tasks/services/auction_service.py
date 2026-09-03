import uuid
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy import select, desc, asc, and_, or_, func
from sqlalchemy.orm import Session

from packages.database.models.auction import Auction, AuctionBid, AuctionClaim, AuctionAuditLog
from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.database.models.ledger import LedgerEntry
from apps.obx_core.services.wallet_service import WalletService
from packages.shared.enums import AuctionType, AuctionStatus, ReferenceType
from packages.shared.exceptions import TaskError, OBXError
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.auction")


class AuctionError(TaskError):
    """Base exception for auction domain errors."""
    pass


def resolve_auction_preview_image(
    banner_url: Optional[str] = None,
    og_image_url: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Optional[str]:
    """Select the main visual image for an auction card using strict priority:
    1. X profile banner image
    2. Project/profile OpenGraph image
    3. X profile avatar as a fallback
    4. None if none is safely available
    """
    for candidate in [banner_url, og_image_url, avatar_url]:
        if candidate and isinstance(candidate, str):
            c_str = candidate.strip()
            if (c_str.startswith("http://") or c_str.startswith("https://")) and not any(ch in c_str for ch in ["\r", "\n", " "]):
                return c_str
    return None


class AuctionService:
    def __init__(self, session: Session):
        self.session = session
        self.wallet_service = WalletService(session)

    def create_auction(
        self,
        title: str,
        reward_title: str,
        description: str,
        auction_type: Optional[AuctionType | str] = None,
        total_slots: int = 1,
        price_or_min_bid: int = 1,
        created_by: str = "SYSTEM",
        starts_at: Optional[datetime] = None,
        ends_at: Optional[datetime] = None,
        external_url: Optional[str] = None,
        project_x_url: Optional[str] = None,
        preview_image_url: Optional[str] = None,
        image_url: Optional[str] = None,
        preview_x_handle: Optional[str] = None,
        preview_x_display_name: Optional[str] = None,
        preview_x_avatar_url: Optional[str] = None,
        preview_x_banner_url: Optional[str] = None,
        preview_x_bio: Optional[str] = None,
        status: AuctionStatus = AuctionStatus.ACTIVE,
    ) -> Auction:
        if not title or not title.strip():
            raise AuctionError("Auction/Project title is required.")
        if not reward_title or not reward_title.strip():
            raise AuctionError("Reward title is required.")
        if not description or not description.strip():
            raise AuctionError("Auction description is required.")
        if total_slots <= 0:
            raise AuctionError("Total slots must be greater than zero.")
        if price_or_min_bid <= 0:
            raise AuctionError("Price / Minimum bid must be greater than zero.")

        if auction_type is None:
            auction_type = AuctionType.GTD
        elif isinstance(auction_type, str):
            try:
                auction_type = AuctionType(auction_type.upper())
            except ValueError:
                raise AuctionError(f"Invalid auction type: '{auction_type}'. Must be 'FCFS' or 'GTD'.")

        now = datetime.now(timezone.utc)
        if starts_at and starts_at > now and status == AuctionStatus.ACTIVE:
            status = AuctionStatus.SCHEDULED

        auction = Auction(
            title=title.strip(),
            reward_title=reward_title.strip(),
            description=description.strip(),
            auction_type=auction_type,
            total_slots=total_slots,
            allocated_slots=0,
            price_or_min_bid=price_or_min_bid,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at,
            external_url=external_url.strip() if external_url else None,
            project_x_url=project_x_url.strip() if project_x_url else None,
            preview_image_url=preview_image_url.strip() if preview_image_url else (image_url.strip() if image_url else None),
            image_url=image_url.strip() if image_url else None,
            preview_x_handle=preview_x_handle.strip() if preview_x_handle else None,
            preview_x_display_name=preview_x_display_name.strip() if preview_x_display_name else None,
            preview_x_avatar_url=preview_x_avatar_url.strip() if preview_x_avatar_url else None,
            preview_x_banner_url=preview_x_banner_url.strip() if preview_x_banner_url else None,
            preview_x_bio=preview_x_bio.strip() if preview_x_bio else None,
            created_by=created_by,
        )
        self.session.add(auction)
        self.session.flush()

        audit = AuctionAuditLog(
            auction_id=auction.id,
            changed_by=created_by,
            action="CREATE_AUCTION",
            old_value=None,
            new_value=f"Type={auction.auction_type.value}, Slots={total_slots}, Price/Min={price_or_min_bid}",
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(auction)

        logger.info("Created auction: ID=%s, Title='%s', Type=%s", auction.id, auction.title, auction.auction_type.value)
        return auction

    def get_auction(self, auction_id: str | uuid.UUID) -> Auction:
        if isinstance(auction_id, str):
            try:
                auction_id = uuid.UUID(auction_id)
            except ValueError:
                raise AuctionError(f"Invalid auction ID: '{auction_id}'")

        auction = self.session.query(Auction).filter(Auction.id == auction_id).first()
        if not auction:
            raise AuctionError(f"Auction '{auction_id}' not found.")
        return auction

    def list_auctions(
        self,
        status: Optional[AuctionStatus] = None,
        auction_type: Optional[AuctionType] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[Auction], int]:
        query = self.session.query(Auction)
        if status:
            query = query.filter(Auction.status == status)
        if auction_type:
            query = query.filter(Auction.auction_type == auction_type)

        total = query.count()
        auctions = query.order_by(desc(Auction.created_at)).offset(offset).limit(limit).all()
        return auctions, total

    def edit_auction_status(
        self,
        auction_id: str | uuid.UUID,
        new_status: AuctionStatus | str,
        changed_by: str,
    ) -> Auction:
        auction = self.get_auction(auction_id)
        if isinstance(new_status, str):
            try:
                new_status = AuctionStatus(new_status.upper())
            except ValueError:
                raise AuctionError(f"Invalid auction status: '{new_status}'")

        if auction.status == new_status:
            return auction

        # State transition validation
        if auction.status == AuctionStatus.COMPLETED:
            raise AuctionError("Cannot modify status of a COMPLETED auction.")
        if auction.status == AuctionStatus.CANCELLED:
            raise AuctionError("Cannot modify status of a CANCELLED auction.")

        old_status_val = auction.status.value

        if new_status == AuctionStatus.CANCELLED:
            return self.cancel_auction(auction_id, cancelled_by=changed_by)

        auction.status = new_status
        audit = AuctionAuditLog(
            auction_id=auction.id,
            changed_by=changed_by,
            action="UPDATE_STATUS",
            old_value=old_status_val,
            new_value=new_status.value,
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(auction)
        logger.info("Updated auction %s status: %s -> %s", auction.id, old_status_val, new_status.value)
        return auction

    def edit_auction(
        self,
        auction_id: str | uuid.UUID,
        changed_by: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        reward_title: Optional[str] = None,
        project_x_url: Optional[str] = None,
        total_slots: Optional[int] = None,
        price_or_min_bid: Optional[int] = None,
        ends_at: Optional[datetime] = None,
    ) -> Auction:
        auction = self.get_auction(auction_id)
        changes = []

        if title is not None and title.strip() and title.strip() != auction.title:
            changes.append(f"Title: {auction.title} -> {title.strip()}")
            auction.title = title.strip()

        if description is not None and description.strip() and description.strip() != auction.description:
            changes.append("Description updated")
            auction.description = description.strip()

        if reward_title is not None and reward_title.strip() and reward_title.strip() != auction.reward_title:
            changes.append(f"Reward: {auction.reward_title} -> {reward_title.strip()}")
            auction.reward_title = reward_title.strip()

        if project_x_url is not None:
            clean_x = project_x_url.strip() if project_x_url.strip() else None
            if clean_x != auction.project_x_url:
                changes.append(f"ProjectXUrl: {auction.project_x_url} -> {clean_x}")
                auction.project_x_url = clean_x

        if total_slots is not None and total_slots > 0 and total_slots != auction.total_slots:
            if total_slots < auction.allocated_slots:
                raise AuctionError(f"Cannot reduce total slots below currently allocated ({auction.allocated_slots}).")
            changes.append(f"TotalSlots: {auction.total_slots} -> {total_slots}")
            auction.total_slots = total_slots

        if price_or_min_bid is not None and price_or_min_bid > 0 and price_or_min_bid != auction.price_or_min_bid:
            changes.append(f"PriceOrMinBid: {auction.price_or_min_bid} -> {price_or_min_bid}")
            auction.price_or_min_bid = price_or_min_bid

        if ends_at is not None and ends_at != auction.ends_at:
            changes.append("EndsAt updated")
            auction.ends_at = ends_at

        if changes:
            audit = AuctionAuditLog(
                auction_id=auction.id,
                changed_by=changed_by,
                action="EDIT_AUCTION",
                old_value=None,
                new_value="; ".join(changes),
            )
            self.session.add(audit)
            self.session.commit()
            self.session.refresh(auction)
            logger.info("Edited auction %s: %s", auction.id, "; ".join(changes))

        return auction

    def update_auction_preview(
        self,
        auction_id: str | uuid.UUID,
        project_x_url: Optional[str] = None,
        handle: Optional[str] = None,
        display_name: Optional[str] = None,
        avatar_url: Optional[str] = None,
        banner_url: Optional[str] = None,
        og_image_url: Optional[str] = None,
        bio: Optional[str] = None,
        preview_image_url: Optional[str] = None,
    ) -> Auction:
        auction = self.get_auction(auction_id)
        if project_x_url is not None:
            auction.project_x_url = project_x_url.strip() if project_x_url.strip() else None
        if handle is not None:
            auction.preview_x_handle = handle.strip() if (handle and handle.strip()) else None
        if display_name is not None:
            auction.preview_x_display_name = display_name.strip() if (display_name and display_name.strip()) else None
        if avatar_url is not None:
            auction.preview_x_avatar_url = avatar_url.strip() if (avatar_url and avatar_url.strip()) else None
        if banner_url is not None:
            auction.preview_x_banner_url = banner_url.strip() if (banner_url and banner_url.strip()) else None
        if bio is not None:
            auction.preview_x_bio = bio.strip() if (bio and bio.strip()) else None

        # Select large project image using strict priority:
        # 1. X profile banner image
        # 2. Project/profile OpenGraph image
        # 3. X profile avatar as a fallback
        # 4. No image if none safely available
        if preview_image_url is not None:
            auction.preview_image_url = preview_image_url
        else:
            chosen = resolve_auction_preview_image(
                banner_url=banner_url,
                og_image_url=og_image_url or auction.image_url,
                avatar_url=avatar_url,
            )
            auction.preview_image_url = chosen

        self.session.commit()
        self.session.refresh(auction)
        logger.info("Updated preview metadata for auction %s: handle=%s, preview_image=%s", auction.id, auction.preview_x_handle, auction.preview_image_url)
        return auction

    def claim_fcfs_slot(self, auction_id: str | uuid.UUID, discord_user_id: str) -> AuctionClaim:
        """Atomically claim a First-Come, First-Served whitelist slot."""
        if not discord_user_id or not str(discord_user_id).strip():
            raise AuctionError("Discord User ID is required.")

        if isinstance(auction_id, str):
            auction_id = uuid.UUID(auction_id)

        stmt = select(Auction).where(Auction.id == auction_id).with_for_update()
        auction = self.session.execute(stmt).scalar_one_or_none()
        if not auction:
            raise AuctionError(f"Auction '{auction_id}' not found.")

        if auction.auction_type != AuctionType.FCFS:
            raise AuctionError(f"Auction '{auction.title}' is not an FCFS auction.")

        now = datetime.now(timezone.utc)
        if auction.status != AuctionStatus.ACTIVE:
            raise AuctionError(f"Auction is not active (current status: {auction.status.value}).")

        starts_at_utc = auction.starts_at if (not auction.starts_at or auction.starts_at.tzinfo) else auction.starts_at.replace(tzinfo=timezone.utc)
        ends_at_utc = auction.ends_at if (not auction.ends_at or auction.ends_at.tzinfo) else auction.ends_at.replace(tzinfo=timezone.utc)

        if starts_at_utc and now < starts_at_utc:
            raise AuctionError(f"Auction has not started yet (starts at <t:{int(starts_at_utc.timestamp())}:R>).")

        if ends_at_utc and now > ends_at_utc:
            raise AuctionError("Auction has ended and is closed for claims.")

        if auction.allocated_slots >= auction.total_slots:
            raise AuctionError("❌ All whitelist slots have already been claimed!")

        # Check for duplicate claim
        existing_claim = (
            self.session.query(AuctionClaim)
            .filter_by(auction_id=auction.id, discord_user_id=discord_user_id)
            .first()
        )
        if existing_claim:
            raise AuctionError("You have already claimed a whitelist slot for this reward.")

        # Perform authoritative wallet debit
        idem_key = f"auction_fcfs:{auction.id}:{discord_user_id}"
        try:
            entry = self.wallet_service.debit(
                discord_user_id=discord_user_id,
                amount=auction.price_or_min_bid,
                reference_type=ReferenceType.AUCTION_FCFS,
                idempotency_key=idem_key,
            )
        except OBXError as exc:
            raise AuctionError(f"Cannot claim WL: {exc.message}")

        # Allocate slot atomically
        auction.allocated_slots += 1
        if auction.allocated_slots >= auction.total_slots:
            auction.status = AuctionStatus.COMPLETED

        claim = AuctionClaim(
            auction_id=auction.id,
            discord_user_id=discord_user_id,
            price_paid=auction.price_or_min_bid,
            obx_transaction_id=entry.id,
        )
        self.session.add(claim)
        self.session.commit()
        self.session.refresh(claim)

        logger.info(
            "FCFS WL Claimed: User=%s, Auction=%s, Price=%d OBX, Slot=%d/%d",
            discord_user_id, auction.id, auction.price_or_min_bid, auction.allocated_slots, auction.total_slots
        )
        return claim

    def place_or_update_gtd_bid(
        self,
        auction_id: str | uuid.UUID,
        discord_user_id: str,
        bid_amount: int,
    ) -> AuctionBid:
        """Place a new bid or update (increase or decrease) an existing bid on a GTD whitelist auction with safe fund locking."""
        if not discord_user_id or not str(discord_user_id).strip():
            raise AuctionError("Discord User ID is required.")
        if bid_amount <= 0:
            raise AuctionError("Bid amount must be a positive integer.")

        if isinstance(auction_id, str):
            auction_id = uuid.UUID(auction_id)

        stmt = select(Auction).where(Auction.id == auction_id).with_for_update()
        auction = self.session.execute(stmt).scalar_one_or_none()
        if not auction:
            raise AuctionError(f"Auction '{auction_id}' not found.")

        if auction.auction_type != AuctionType.GTD:
            raise AuctionError(f"Auction '{auction.title}' is not a GTD bidding auction.")

        now = datetime.now(timezone.utc)
        if auction.status != AuctionStatus.ACTIVE:
            raise AuctionError(f"Auction is not open for bidding (current status: {auction.status.value}).")

        starts_at_utc = auction.starts_at if (not auction.starts_at or auction.starts_at.tzinfo) else auction.starts_at.replace(tzinfo=timezone.utc)
        ends_at_utc = auction.ends_at if (not auction.ends_at or auction.ends_at.tzinfo) else auction.ends_at.replace(tzinfo=timezone.utc)

        if starts_at_utc and now < starts_at_utc:
            raise AuctionError(f"Auction has not started yet (starts <t:{int(starts_at_utc.timestamp())}:R>).")

        if ends_at_utc and now > ends_at_utc:
            raise AuctionError("Auction bidding has closed.")

        if bid_amount < auction.price_or_min_bid:
            raise AuctionError(f"Bid amount must be at least the minimum bid of {auction.price_or_min_bid:,} OBX.")

        # Check existing bid
        existing_bid = (
            self.session.query(AuctionBid)
            .filter_by(auction_id=auction.id, discord_user_id=discord_user_id)
            .with_for_update()
            .first()
        )

        if existing_bid:
            if bid_amount == existing_bid.bid_amount:
                return existing_bid

            if bid_amount > existing_bid.bid_amount:
                # Increasing bid: Lock the delta difference
                delta = bid_amount - existing_bid.bid_amount
                idem_key = f"auction_gtd_bid_inc:{auction.id}:{discord_user_id}:{bid_amount}"
                try:
                    self.wallet_service.lock_funds(
                        discord_user_id=discord_user_id,
                        amount=delta,
                        reference_type=ReferenceType.AUCTION_BID,
                        idempotency_key=idem_key,
                    )
                except OBXError as exc:
                    raise AuctionError(f"Cannot increase bid: {exc.message}")
            else:
                # Decreasing bid: Unlock the delta difference back to available
                delta = existing_bid.bid_amount - bid_amount
                idem_key = f"auction_gtd_bid_dec:{auction.id}:{discord_user_id}:{bid_amount}"
                try:
                    self.wallet_service.release_funds(
                        discord_user_id=discord_user_id,
                        amount=delta,
                        reference_type=ReferenceType.AUCTION_BID,
                        idempotency_key=idem_key,
                    )
                except OBXError as exc:
                    raise AuctionError(f"Cannot lower bid: {exc.message}")

            existing_bid.bid_amount = bid_amount
            existing_bid.updated_at = now
            self.session.commit()
            self.session.refresh(existing_bid)
            logger.info("Updated GTD bid: User=%s, Auction=%s, New Bid=%d OBX", discord_user_id, auction.id, bid_amount)
            return existing_bid
        else:
            # First bid: Lock full bid amount
            idem_key = f"auction_gtd_bid_new:{auction.id}:{discord_user_id}:{bid_amount}"
            try:
                self.wallet_service.lock_funds(
                    discord_user_id=discord_user_id,
                    amount=bid_amount,
                    reference_type=ReferenceType.AUCTION_BID,
                    idempotency_key=idem_key,
                )
            except OBXError as exc:
                raise AuctionError(f"Cannot place bid: {exc.message}")

            bid = AuctionBid(
                auction_id=auction.id,
                discord_user_id=discord_user_id,
                bid_amount=bid_amount,
            )
            self.session.add(bid)
            self.session.commit()
            self.session.refresh(bid)
            logger.info("Placed new GTD bid: User=%s, Auction=%s, Amount=%d OBX", discord_user_id, auction.id, bid_amount)
            return bid

    def get_auction_standings(
        self,
        auction_id: str | uuid.UUID,
        discord_user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch real-time ranked standings, cutoff price, and user winning status."""
        auction = self.get_auction(auction_id)

        # Ranked bids query:
        # 1. bid_amount DESC
        # 2. updated_at ASC (earliest bid timestamp wins ties)
        # 3. discord_user_id ASC (deterministic tie-breaker)
        bids = (
            self.session.query(AuctionBid)
            .filter_by(auction_id=auction.id)
            .order_by(
                desc(AuctionBid.bid_amount),
                asc(AuctionBid.updated_at),
                asc(AuctionBid.discord_user_id),
            )
            .all()
        )

        total_bidders = len(bids)
        total_slots = auction.total_slots

        # Cutoff is the bid of the N-th user (if >= N bids exist) or minimum bid
        if total_bidders >= total_slots and total_slots > 0:
            winning_cutoff = bids[total_slots - 1].bid_amount
        else:
            winning_cutoff = auction.price_or_min_bid

        user_rank = None
        user_bid_amount = None
        is_winning = False

        if discord_user_id:
            for idx, b in enumerate(bids, start=1):
                if b.discord_user_id == str(discord_user_id):
                    user_rank = idx
                    user_bid_amount = b.bid_amount
                    is_winning = (idx <= total_slots)
                    break

        return {
            "auction": auction,
            "ranked_bids": bids,
            "total_bidders": total_bidders,
            "total_slots": total_slots,
            "winning_cutoff": winning_cutoff,
            "user_rank": user_rank,
            "user_bid_amount": user_bid_amount,
            "is_winning": is_winning,
        }

    def settle_and_finalize_auction(
        self,
        auction_id: str | uuid.UUID,
        finalized_by: Optional[str] = None,
    ) -> Tuple[Auction, List[AuctionBid], List[AuctionBid]]:
        """Deterministically settle GTD auction: top N highest valid bids win; non-winners are fully unlocked."""
        if isinstance(auction_id, str):
            auction_id = uuid.UUID(auction_id)

        stmt = select(Auction).where(Auction.id == auction_id).with_for_update()
        auction = self.session.execute(stmt).scalar_one_or_none()
        if not auction:
            raise AuctionError(f"Auction '{auction_id}' not found.")

        if auction.status == AuctionStatus.COMPLETED:
            bids = self.session.query(AuctionBid).filter_by(auction_id=auction.id).all()
            winners = [b for b in bids if b.is_winner is True]
            losers = [b for b in bids if b.is_winner is False]
            return auction, winners, losers

        if auction.status == AuctionStatus.CANCELLED:
            raise AuctionError("Cannot finalize a CANCELLED auction.")

        auction.status = AuctionStatus.SETTLING
        self.session.flush()

        winners: List[AuctionBid] = []
        losers: List[AuctionBid] = []

        if auction.auction_type == AuctionType.GTD:
            bids_stmt = (
                select(AuctionBid)
                .where(AuctionBid.auction_id == auction.id)
                .order_by(
                    desc(AuctionBid.bid_amount),
                    asc(AuctionBid.updated_at),
                    asc(AuctionBid.discord_user_id),
                )
                .with_for_update()
            )
            bids = self.session.execute(bids_stmt).scalars().all()

            winner_count = min(len(bids), auction.total_slots)
            winners = bids[:winner_count]
            losers = bids[winner_count:]

            # Settle Winners (Pay-As-Bid)
            for w_bid in winners:
                if not w_bid.is_settled:
                    rel_idem = f"auction_win_rel:{auction.id}:{w_bid.discord_user_id}"
                    deb_idem = f"auction_win_deb:{auction.id}:{w_bid.discord_user_id}"

                    self.wallet_service.release_funds(
                        discord_user_id=w_bid.discord_user_id,
                        amount=w_bid.bid_amount,
                        reference_type=ReferenceType.AUCTION_WIN,
                        idempotency_key=rel_idem,
                    )
                    entry = self.wallet_service.debit(
                        discord_user_id=w_bid.discord_user_id,
                        amount=w_bid.bid_amount,
                        reference_type=ReferenceType.AUCTION_WIN,
                        idempotency_key=deb_idem,
                    )

                    w_bid.is_winner = True
                    w_bid.is_settled = True

                    claim = AuctionClaim(
                        auction_id=auction.id,
                        discord_user_id=w_bid.discord_user_id,
                        price_paid=w_bid.bid_amount,
                        obx_transaction_id=entry.id,
                    )
                    self.session.add(claim)
                    auction.allocated_slots += 1

            # Settle Non-Winners (Full locked bid is returned to available balance)
            for l_bid in losers:
                if not l_bid.is_settled:
                    rel_idem = f"auction_lose_rel:{auction.id}:{l_bid.discord_user_id}"
                    self.wallet_service.release_funds(
                        discord_user_id=l_bid.discord_user_id,
                        amount=l_bid.bid_amount,
                        reference_type=ReferenceType.AUCTION_REFUND,
                        idempotency_key=rel_idem,
                    )
                    l_bid.is_winner = False
                    l_bid.is_settled = True

        auction.status = AuctionStatus.COMPLETED
        audit = AuctionAuditLog(
            auction_id=auction.id,
            changed_by=finalized_by or "SYSTEM",
            action="FINALIZE_AUCTION",
            old_value="SETTLING",
            new_value=f"COMPLETED (Allocated {auction.allocated_slots}/{auction.total_slots})",
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(auction)

        logger.info("Finalized auction: %s (Allocated %d/%d slots)", auction.id, auction.allocated_slots, auction.total_slots)
        return auction, winners, losers

    def cancel_auction(self, auction_id: str | uuid.UUID, cancelled_by: str) -> Auction:
        """Cancel auction and safely refund/release all locked GTD bids."""
        if isinstance(auction_id, str):
            auction_id = uuid.UUID(auction_id)

        stmt = select(Auction).where(Auction.id == auction_id).with_for_update()
        auction = self.session.execute(stmt).scalar_one_or_none()
        if not auction:
            raise AuctionError(f"Auction '{auction_id}' not found.")

        if auction.status == AuctionStatus.CANCELLED:
            return auction
        if auction.status == AuctionStatus.COMPLETED:
            raise AuctionError("Cannot cancel an already COMPLETED auction.")

        old_status_val = auction.status.value

        # If GTD: safely release all unsettled bids
        if auction.auction_type == AuctionType.GTD:
            bids = (
                self.session.query(AuctionBid)
                .filter_by(auction_id=auction.id, is_settled=False)
                .with_for_update()
                .all()
            )
            for bid in bids:
                rel_idem = f"auction_cancel_rel:{auction.id}:{bid.discord_user_id}"
                self.wallet_service.release_funds(
                    discord_user_id=bid.discord_user_id,
                    amount=bid.bid_amount,
                    reference_type=ReferenceType.AUCTION_REFUND,
                    idempotency_key=rel_idem,
                )
                bid.is_settled = True
                bid.is_winner = False

        auction.status = AuctionStatus.CANCELLED
        audit = AuctionAuditLog(
            auction_id=auction.id,
            changed_by=cancelled_by,
            action="CANCEL_AUCTION",
            old_value=old_status_val,
            new_value="CANCELLED",
        )
        self.session.add(audit)
        self.session.commit()
        self.session.refresh(auction)

        logger.info("Cancelled auction %s by %s", auction.id, cancelled_by)
        return auction

    def grant_custom_reward(
        self,
        admin_discord_id: str,
        target_discord_id: str,
        amount: int,
        reason: str,
    ) -> LedgerEntry:
        """Grant custom OBX reward to a community member with double-entry ledger audit."""
        if not target_discord_id or not str(target_discord_id).strip():
            raise AuctionError("Target Discord User ID is required.")
        if amount <= 0:
            raise AuctionError("Reward amount must be a positive integer.")
        if not reason or not reason.strip():
            raise AuctionError("Reason/note is required for custom admin grants.")

        idem_key = f"admin_grant:{admin_discord_id}:{target_discord_id}:{uuid.uuid4()}"
        entry = self.wallet_service.credit(
            discord_user_id=target_discord_id,
            amount=amount,
            reference_type=ReferenceType.ADMIN,
            idempotency_key=idem_key,
        )
        logger.info(
            "Admin custom reward granted: Admin=%s, Target=%s, Amount=%d OBX, Reason='%s'",
            admin_discord_id, target_discord_id, amount, reason
        )
        return entry

    def get_user_auction_activity(self, discord_user_id: str) -> Dict[str, Any]:
        """Fetch active bids, FCFS claims, and whitelist wins for a member."""
        bids = (
            self.session.query(AuctionBid)
            .filter_by(discord_user_id=discord_user_id)
            .order_by(desc(AuctionBid.updated_at))
            .all()
        )
        claims = (
            self.session.query(AuctionClaim)
            .filter_by(discord_user_id=discord_user_id)
            .order_by(desc(AuctionClaim.claimed_at))
            .all()
        )

        active_bids = [b for b in bids if not b.is_settled and b.auction.status == AuctionStatus.ACTIVE]
        wins = [c for c in claims]

        return {
            "active_bids": active_bids,
            "claims": claims,
            "wins": wins,
            "total_bids": len(bids),
            "total_wins": len(wins),
        }

    def auto_expire_and_settle_auctions(self) -> List[Tuple[Auction, List[AuctionBid], int]]:
        """Background maintenance: finds expired active auctions and finalizes/settles them."""
        now = datetime.now(timezone.utc)
        expired_auctions = (
            self.session.query(Auction)
            .filter(
                Auction.status == AuctionStatus.ACTIVE,
                Auction.ends_at.isnot(None),
                Auction.ends_at <= now,
            )
            .all()
        )

        results = []
        for auc in expired_auctions:
            try:
                if auc.auction_type == AuctionType.GTD:
                    auc_obj, winners, losers = self.settle_and_finalize_auction(auc.id, finalized_by="SYSTEM_AUTO_SETTLE")
                    total_bidders = len(winners) + len(losers)
                    results.append((auc_obj, winners, total_bidders))
                else:  # FCFS
                    auc.status = AuctionStatus.COMPLETED
                    audit = AuctionAuditLog(
                        auction_id=auc.id,
                        changed_by="SYSTEM_AUTO_SETTLE",
                        action="EXPIRE_AUCTION",
                        old_value="ACTIVE",
                        new_value=f"COMPLETED (Sold {auc.allocated_slots}/{auc.total_slots})",
                    )
                    self.session.add(audit)
                    self.session.commit()
                    self.session.refresh(auc)
                    results.append((auc, [], auc.allocated_slots))
            except Exception as e:
                logger.error("Error auto-settling auction %s: %s", auc.id, e)

        return results
