from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select

from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.database.models.ledger import LedgerEntry
from packages.shared.enums import TransactionType
from packages.shared.exceptions import UserNotFoundError
from packages.shared.logging import get_logger

logger = get_logger("obx.reconciliation")


@dataclass
class UserDiscrepancy:
    discord_user_id: str
    user_id: UUID
    actual_available: int
    expected_available: int
    actual_locked: int
    expected_locked: int
    available_diff: int
    locked_diff: int
    ledger_entry_count: int


@dataclass
class ReconciliationReport:
    is_consistent: bool
    total_users_checked: int
    mismatched_users_count: int
    discrepancies: List[UserDiscrepancy] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        if self.is_consistent:
            return (
                f"Reconciliation SUCCESS: All {self.total_users_checked} user wallets "
                f"match ledger history perfectly."
            )
        return (
            f"Reconciliation FAILED: Found {self.mismatched_users_count} mismatched wallets "
            f"out of {self.total_users_checked} checked."
        )


class ReconciliationService:
    def __init__(self, session: Session):
        self.session = session

    def reconcile_user(self, discord_user_id: str) -> Optional[UserDiscrepancy]:
        """Reconciles a single user's wallet against their ledger entries."""
        user = self.session.execute(
            select(User).where(User.discord_user_id == discord_user_id)
        ).scalar_one_or_none()

        if not user:
            raise UserNotFoundError(discord_user_id)

        wallet = user.wallet
        if not wallet:
            actual_available = 0
            actual_locked = 0
        else:
            actual_available = wallet.available_balance
            actual_locked = wallet.locked_balance

        entries = self.session.execute(
            select(LedgerEntry).where(LedgerEntry.user_id == user.id)
        ).scalars().all()

        expected_available = 0
        expected_locked = 0

        for entry in entries:
            amount = entry.amount
            tx_type = entry.transaction_type
            if isinstance(tx_type, str):
                tx_type = TransactionType(tx_type)

            if tx_type == TransactionType.CREDIT:
                expected_available += amount
            elif tx_type == TransactionType.DEBIT:
                expected_available -= amount
            elif tx_type == TransactionType.LOCK:
                expected_available -= amount
                expected_locked += amount
            elif tx_type == TransactionType.RELEASE:
                expected_locked -= amount
                expected_available += amount
            elif tx_type == TransactionType.SETTLEMENT:
                expected_locked -= amount
            elif tx_type == TransactionType.REFUND:
                expected_available += amount

        avail_diff = actual_available - expected_available
        locked_diff = actual_locked - expected_locked

        if avail_diff != 0 or locked_diff != 0:
            return UserDiscrepancy(
                discord_user_id=discord_user_id,
                user_id=user.id,
                actual_available=actual_available,
                expected_available=expected_available,
                actual_locked=actual_locked,
                expected_locked=expected_locked,
                available_diff=avail_diff,
                locked_diff=locked_diff,
                ledger_entry_count=len(entries),
            )
        return None

    def reconcile_all(self) -> ReconciliationReport:
        """Runs a complete system-wide reconciliation across all registered users."""
        users = self.session.execute(select(User)).scalars().all()
        discrepancies: List[UserDiscrepancy] = []

        for user in users:
            disc = self.reconcile_user(user.discord_user_id)
            if disc:
                discrepancies.append(disc)

        is_consistent = len(discrepancies) == 0
        report = ReconciliationReport(
            is_consistent=is_consistent,
            total_users_checked=len(users),
            mismatched_users_count=len(discrepancies),
            discrepancies=discrepancies,
        )

        if is_consistent:
            logger.info("System reconciliation passed for %d users.", len(users))
        else:
            logger.error(
                "System reconciliation failed: %d discrepancies found.",
                len(discrepancies),
            )

        return report
