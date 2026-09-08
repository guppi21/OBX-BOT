from __future__ import annotations
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Optional, Tuple, NamedTuple, Dict, Any
from dataclasses import dataclass
from sqlalchemy import select, func, desc, asc, and_, or_
from sqlalchemy.orm import Session

from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.database.models.submission import TaskSubmission
from packages.shared.enums import SubmissionStatus
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.leaderboard")


class LeaderboardCategory(str, Enum):
    TOTAL_OBX = "TOTAL_OBX"
    TASK_EARNINGS = "TASK_EARNINGS"
    TASK_COMPLETIONS = "TASK_COMPLETIONS"
    ACTIVITY = "ACTIVITY"


class LeaderboardPeriod(str, Enum):
    ALL_TIME = "ALL_TIME"
    THIS_MONTH = "THIS_MONTH"
    THIS_WEEK = "THIS_WEEK"


@dataclass
class LeaderboardEntry:
    rank: int
    discord_user_id: str
    score: int
    total_balance: int = 0
    task_earnings: int = 0
    tasks_completed: int = 0


@dataclass
class UserLeaderboardPosition:
    discord_user_id: str
    rank: Optional[int]
    score: int
    total_balance: int
    task_earnings: int
    tasks_completed: int
    total_participants: int


class LeaderboardService:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def get_period_start(period: LeaderboardPeriod) -> Optional[datetime]:
        now = datetime.now(timezone.utc)
        if period == LeaderboardPeriod.THIS_WEEK:
            # Start of current week (Monday 00:00 UTC)
            start_of_week = now - timedelta(days=now.weekday())
            return start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == LeaderboardPeriod.THIS_MONTH:
            # Start of current month (1st of month 00:00 UTC)
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return None

    def get_leaderboard(
        self,
        category: LeaderboardCategory = LeaderboardCategory.TOTAL_OBX,
        period: LeaderboardPeriod = LeaderboardPeriod.ALL_TIME,
        limit: int = 10,
        offset: int = 0,
    ) -> Tuple[List[LeaderboardEntry], int]:
        """Fetch paginated leaderboard rankings for a category and time period."""
        if category == LeaderboardCategory.TOTAL_OBX:
            return self._get_total_obx_leaderboard(limit=limit, offset=offset)
        elif category == LeaderboardCategory.TASK_EARNINGS:
            return self._get_task_earnings_leaderboard(period=period, limit=limit, offset=offset)
        elif category == LeaderboardCategory.TASK_COMPLETIONS:
            return self._get_task_completions_leaderboard(period=period, limit=limit, offset=offset)
        elif category == LeaderboardCategory.ACTIVITY:
            return self._get_activity_leaderboard(period=period, limit=limit, offset=offset)
        return [], 0

    def _get_total_obx_leaderboard(self, limit: int = 10, offset: int = 0) -> Tuple[List[LeaderboardEntry], int]:
        # Count total participants with balance > 0
        total_count = (
            self.session.query(func.count(Wallet.id))
            .filter((Wallet.available_balance + Wallet.locked_balance) > 0)
            .scalar()
            or 0
        )

        # Query top wallets
        stmt = (
            select(
                User.discord_user_id,
                (Wallet.available_balance + Wallet.locked_balance).label("total_bal"),
            )
            .join(Wallet, Wallet.user_id == User.id)
            .where((Wallet.available_balance + Wallet.locked_balance) > 0)
            .order_by(
                desc(Wallet.available_balance + Wallet.locked_balance),
                asc(User.created_at),
                asc(User.id),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()

        entries = []
        for idx, row in enumerate(rows, start=offset + 1):
            user_id = row[0]
            bal = int(row[1])

            entries.append(
                LeaderboardEntry(
                    rank=idx,
                    discord_user_id=user_id,
                    score=bal,
                    total_balance=bal,
                    task_earnings=0,
                    tasks_completed=0,
                )
            )

        return entries, total_count

    def _get_task_earnings_leaderboard(
        self,
        period: LeaderboardPeriod,
        limit: int = 10,
        offset: int = 0,
    ) -> Tuple[List[LeaderboardEntry], int]:
        period_start = self.get_period_start(period)

        base_filter = [TaskSubmission.status == SubmissionStatus.APPROVED]
        if period_start:
            base_filter.append(TaskSubmission.reviewed_at >= period_start)

        # Count total distinct users with earnings in period
        count_stmt = (
            select(func.count(func.distinct(TaskSubmission.discord_user_id)))
            .where(and_(*base_filter))
        )
        total_count = self.session.execute(count_stmt).scalar() or 0

        # Query aggregated earnings
        stmt = (
            select(
                TaskSubmission.discord_user_id,
                func.sum(TaskSubmission.reward_amount).label("earnings"),
                func.count(TaskSubmission.id).label("task_count"),
                func.min(TaskSubmission.reviewed_at).label("earliest_review"),
            )
            .where(and_(*base_filter))
            .group_by(TaskSubmission.discord_user_id)
            .order_by(
                desc(func.sum(TaskSubmission.reward_amount)),
                asc(func.min(TaskSubmission.reviewed_at)),
                asc(TaskSubmission.discord_user_id),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()

        entries = []
        for idx, row in enumerate(rows, start=offset + 1):
            user_id = row[0]
            earnings = int(row[1] or 0)
            count = int(row[2] or 0)
            bal = self._get_user_total_balance(user_id)
            entries.append(
                LeaderboardEntry(
                    rank=idx,
                    discord_user_id=user_id,
                    score=earnings,
                    total_balance=bal,
                    task_earnings=earnings,
                    tasks_completed=count,
                )
            )
        return entries, total_count

    def _get_task_completions_leaderboard(
        self,
        period: LeaderboardPeriod,
        limit: int = 10,
        offset: int = 0,
    ) -> Tuple[List[LeaderboardEntry], int]:
        period_start = self.get_period_start(period)

        base_filter = [TaskSubmission.status == SubmissionStatus.APPROVED]
        if period_start:
            base_filter.append(TaskSubmission.reviewed_at >= period_start)

        count_stmt = (
            select(func.count(func.distinct(TaskSubmission.discord_user_id)))
            .where(and_(*base_filter))
        )
        total_count = self.session.execute(count_stmt).scalar() or 0

        stmt = (
            select(
                TaskSubmission.discord_user_id,
                func.count(TaskSubmission.id).label("task_count"),
                func.coalesce(func.sum(TaskSubmission.reward_amount), 0).label("earnings"),
                func.min(TaskSubmission.reviewed_at).label("earliest_review"),
            )
            .where(and_(*base_filter))
            .group_by(TaskSubmission.discord_user_id)
            .order_by(
                desc(func.count(TaskSubmission.id)),
                desc(func.sum(TaskSubmission.reward_amount)),
                asc(func.min(TaskSubmission.reviewed_at)),
                asc(TaskSubmission.discord_user_id),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()

        entries = []
        for idx, row in enumerate(rows, start=offset + 1):
            user_id = row[0]
            count = int(row[1] or 0)
            earnings = int(row[2] or 0)
            bal = self._get_user_total_balance(user_id)
            entries.append(
                LeaderboardEntry(
                    rank=idx,
                    discord_user_id=user_id,
                    score=count,
                    total_balance=bal,
                    task_earnings=earnings,
                    tasks_completed=count,
                )
            )
        return entries, total_count

    def _get_activity_leaderboard(
        self,
        period: LeaderboardPeriod,
        limit: int = 10,
        offset: int = 0,
    ) -> Tuple[List[LeaderboardEntry], int]:
        period_start = self.get_period_start(period)

        base_filter = [TaskSubmission.status == SubmissionStatus.APPROVED]
        if period_start:
            base_filter.append(TaskSubmission.reviewed_at >= period_start)

        count_stmt = (
            select(func.count(func.distinct(TaskSubmission.discord_user_id)))
            .where(and_(*base_filter))
        )
        total_count = self.session.execute(count_stmt).scalar() or 0

        # Activity Score = (completions * 50) + (earnings)
        activity_expr = (func.count(TaskSubmission.id) * 50) + func.coalesce(func.sum(TaskSubmission.reward_amount), 0)

        stmt = (
            select(
                TaskSubmission.discord_user_id,
                activity_expr.label("activity_score"),
                func.count(TaskSubmission.id).label("task_count"),
                func.coalesce(func.sum(TaskSubmission.reward_amount), 0).label("earnings"),
            )
            .where(and_(*base_filter))
            .group_by(TaskSubmission.discord_user_id)
            .order_by(
                desc(activity_expr),
                asc(TaskSubmission.discord_user_id),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()

        entries = []
        for idx, row in enumerate(rows, start=offset + 1):
            user_id = row[0]
            score = int(row[1] or 0)
            count = int(row[2] or 0)
            earnings = int(row[3] or 0)
            bal = self._get_user_total_balance(user_id)
            entries.append(
                LeaderboardEntry(
                    rank=idx,
                    discord_user_id=user_id,
                    score=score,
                    total_balance=bal,
                    task_earnings=earnings,
                    tasks_completed=count,
                )
            )
        return entries, total_count

    def get_user_position(
        self,
        discord_user_id: str,
        category: LeaderboardCategory = LeaderboardCategory.TOTAL_OBX,
        period: LeaderboardPeriod = LeaderboardPeriod.ALL_TIME,
    ) -> UserLeaderboardPosition:
        """Calculate the exact global position and summary stats for a specific user."""
        earnings, count = self._get_user_approved_task_stats(discord_user_id, period=period)

        if category == LeaderboardCategory.TOTAL_OBX:
            user = self.session.query(User).filter_by(discord_user_id=str(discord_user_id)).first()
            user_bal = 0
            user_rank = None
            if user and user.wallet:
                user_bal = (user.wallet.available_balance or 0) + (user.wallet.locked_balance or 0)

            total_participants = (
                self.session.query(func.count(Wallet.id))
                .filter((Wallet.available_balance + Wallet.locked_balance) > 0)
                .scalar()
                or 0
            )

            if user_bal > 0 and user:
                tot = Wallet.available_balance + Wallet.locked_balance
                tie_filter = and_(
                    User.discord_user_id != str(discord_user_id),
                    or_(
                        tot > user_bal,
                        and_(
                            tot == user_bal,
                            or_(
                                User.created_at < user.created_at,
                                and_(User.created_at == user.created_at, User.id < user.id),
                            ),
                        ),
                    ),
                )
                higher_count = (
                    self.session.query(func.count(Wallet.id))
                    .join(User, User.id == Wallet.user_id)
                    .filter(tot > 0, tie_filter)
                    .scalar()
                    or 0
                )
                user_rank = higher_count + 1

            return UserLeaderboardPosition(
                discord_user_id=str(discord_user_id),
                rank=user_rank,
                score=user_bal,
                total_balance=user_bal,
                task_earnings=earnings,
                tasks_completed=count,
                total_participants=total_participants,
            )

        # Fallback for other categories (TASK_EARNINGS, etc.)
        bal = self._get_user_total_balance(discord_user_id)
        all_entries, total_participants = self.get_leaderboard(
            category=category,
            period=period,
            limit=10000,
            offset=0,
        )

        user_rank = None
        user_score = 0
        for entry in all_entries:
            if entry.discord_user_id == discord_user_id:
                user_rank = entry.rank
                user_score = entry.score
                break

        if user_score == 0:
            if category == LeaderboardCategory.TASK_EARNINGS:
                user_score = earnings
            elif category == LeaderboardCategory.TASK_COMPLETIONS:
                user_score = count
            elif category == LeaderboardCategory.ACTIVITY:
                user_score = (count * 50) + earnings

        return UserLeaderboardPosition(
            discord_user_id=discord_user_id,
            rank=user_rank,
            score=user_score,
            total_balance=bal,
            task_earnings=earnings,
            tasks_completed=count,
            total_participants=total_participants,
        )

    def _get_user_total_balance(self, discord_user_id: str) -> int:
        stmt = (
            select(Wallet.available_balance + Wallet.locked_balance)
            .join(User, User.id == Wallet.user_id)
            .where(User.discord_user_id == discord_user_id)
        )
        return int(self.session.execute(stmt).scalar() or 0)

    def _get_user_approved_task_stats(
        self,
        discord_user_id: str,
        period: Optional[LeaderboardPeriod] = None,
    ) -> Tuple[int, int]:
        filters = [
            TaskSubmission.discord_user_id == discord_user_id,
            TaskSubmission.status == SubmissionStatus.APPROVED,
        ]
        if period:
            start = self.get_period_start(period)
            if start:
                filters.append(TaskSubmission.reviewed_at >= start)

        stmt = (
            select(
                func.coalesce(func.sum(TaskSubmission.reward_amount), 0),
                func.count(TaskSubmission.id),
            )
            .where(and_(*filters))
        )
        row = self.session.execute(stmt).first()
        if row:
            return int(row[0] or 0), int(row[1] or 0)
        return 0, 0

    def clear_leaderboard_data(self) -> Dict[str, int]:
        """Reset all raider balances, earnings, and submission history to produce a clean leaderboard."""
        from packages.database.models.wallet import Wallet
        from packages.database.models.ledger import LedgerEntry
        from packages.database.models.submission import TaskSubmission
        from packages.database.models.submission_audit_log import SubmissionAuditLog
        from packages.database.models.auction import AuctionBid, AuctionClaim
        from packages.database.models.task import Task

        # 1. Reset all wallet balances to 0
        wallets_updated = self.session.query(Wallet).update(
            {"available_balance": 0, "locked_balance": 0},
            synchronize_session=False,
        )

        # 2. Delete ledger transactions
        ledger_deleted = self.session.query(LedgerEntry).delete(synchronize_session=False)

        # 3. Delete submission audit logs and task submissions
        sub_audits_deleted = self.session.query(SubmissionAuditLog).delete(synchronize_session=False)
        subs_deleted = self.session.query(TaskSubmission).delete(synchronize_session=False)

        # 4. Reset task distribution counters so tasks can be completed fresh
        tasks_reset = self.session.query(Task).update(
            {"distributed_reward": 0},
            synchronize_session=False,
        )

        # 5. Delete auction bids & claims so no locked funds or winner records linger
        claims_deleted = self.session.query(AuctionClaim).delete(synchronize_session=False)
        bids_deleted = self.session.query(AuctionBid).delete(synchronize_session=False)

        self.session.commit()
        self.session.expire_all()
        logger.info(
            "Leaderboard data cleared: %d wallets reset, %d ledger entries, %d submissions, %d tasks reset",
            wallets_updated, ledger_deleted, subs_deleted, tasks_reset,
        )
        return {
            "wallets_reset": wallets_updated,
            "submissions_cleared": subs_deleted,
            "ledger_cleared": ledger_deleted,
            "tasks_reset": tasks_reset,
            "bids_cleared": bids_deleted,
        }

