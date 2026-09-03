from typing import Tuple, List
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func

from packages.database.models.user import User
from packages.database.models.wallet import Wallet
from packages.database.models.ledger import LedgerEntry
from packages.shared.enums import TransactionType
from packages.shared.exceptions import (
    UserNotFoundError,
    WalletNotFoundError,
    InsufficientFundsError,
    InvalidAmountError,
    IdempotencyConflictError,
)
from packages.shared.logging import get_logger

logger = get_logger("obx.wallet_service")


class WalletService:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create_user(self, discord_user_id: str, commit: bool = True) -> Tuple[User, Wallet, bool]:
        """Safely retrieves an existing user or creates a new user with an initialized wallet.
        
        Returns:
            Tuple of (User, Wallet, created: bool)
        """
        if not discord_user_id or not isinstance(discord_user_id, str):
            raise ValueError("discord_user_id must be a non-empty string.")

        discord_user_id = discord_user_id.strip()

        # Check for existing user with eager loaded wallet
        user = self.session.execute(
            select(User).options(joinedload(User.wallet)).where(User.discord_user_id == discord_user_id)
        ).unique().scalar_one_or_none()

        if user:
            if not user.wallet:
                wallet = Wallet(user_id=user.id, available_balance=0, locked_balance=0)
                self.session.add(wallet)
                if commit:
                    self.session.commit()
                    self.session.refresh(user)
                else:
                    self.session.flush()
            return user, user.wallet, False

        # Create user and wallet atomically
        user = User(discord_user_id=discord_user_id)
        self.session.add(user)
        try:
            self.session.flush()
            wallet = Wallet(user_id=user.id, available_balance=0, locked_balance=0)
            self.session.add(wallet)
            if commit:
                self.session.commit()
                self.session.refresh(user)
            else:
                self.session.flush()
            logger.info("Created user and wallet for Discord ID: %s", discord_user_id)
            return user, user.wallet, True
        except IntegrityError:
            self.session.rollback()
            # Concurrently created by another transaction
            user = self.session.execute(
                select(User).options(joinedload(User.wallet)).where(User.discord_user_id == discord_user_id)
            ).unique().scalar_one()
            return user, user.wallet, False

    def get_user(self, discord_user_id: str) -> User:
        """Retrieves a user by Discord ID or raises UserNotFoundError."""
        user = self.session.execute(
            select(User).options(joinedload(User.wallet)).where(User.discord_user_id == str(discord_user_id).strip())
        ).unique().scalar_one_or_none()

        if not user:
            raise UserNotFoundError(discord_user_id)
        return user

    def get_wallet(self, discord_user_id: str) -> Wallet:
        """Retrieves a user's wallet by Discord ID."""
        user = self.get_user(discord_user_id)
        if not user.wallet:
            raise WalletNotFoundError(discord_user_id)
        return user.wallet

    def get_balance(self, discord_user_id: str) -> dict:
        """Gets detailed balance for a user."""
        wallet = self.get_wallet(discord_user_id)
        return {
            "discord_user_id": discord_user_id,
            "available_balance": wallet.available_balance,
            "locked_balance": wallet.locked_balance,
            "total_balance": wallet.total_balance,
            "updated_at": wallet.updated_at,
        }

    def _validate_amount(self, amount: int) -> None:
        if not isinstance(amount, int) or amount <= 0:
            raise InvalidAmountError(amount)

    def _check_idempotency(
        self,
        idempotency_key: str,
        user_id: str,
        expected_type: TransactionType,
        expected_amount: int,
    ) -> LedgerEntry | None:
        """Checks if a transaction with the given idempotency key already exists."""
        existing = self.session.execute(
            select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
        ).scalar_one_or_none()

        if not existing:
            return None

        existing_tx_type = existing.transaction_type.value if hasattr(existing.transaction_type, "value") else str(existing.transaction_type)
        expected_tx_type = expected_type.value if hasattr(expected_type, "value") else str(expected_type)

        if (
            str(existing.user_id) != str(user_id)
            or existing.amount != expected_amount
            or existing_tx_type != expected_tx_type
        ):
            raise IdempotencyConflictError(
                idempotency_key,
                reason=(
                    f"Existing record: user={existing.user_id}, type={existing_tx_type}, "
                    f"amount={existing.amount}; Requested: user={user_id}, type={expected_tx_type}, "
                    f"amount={expected_amount}"
                ),
            )

        logger.info("Idempotent hit for key: %s (Entry ID: %s)", idempotency_key, existing.id)
        return existing

    def _lock_wallet_row(self, wallet_id) -> Wallet:
        """Locks and refreshes the wallet row for concurrent safety with populate_existing."""
        try:
            wallet = self.session.execute(
                select(Wallet)
                .where(Wallet.id == wallet_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one()
        except Exception:
            wallet = self.session.execute(
                select(Wallet)
                .where(Wallet.id == wallet_id)
                .execution_options(populate_existing=True)
            ).scalar_one()
        return wallet

    def credit(
        self,
        discord_user_id: str,
        amount: int,
        reference_type: str,
        idempotency_key: str,
        reference_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> LedgerEntry:
        """Credits funds to user's available balance atomically."""
        self._validate_amount(amount)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")

        user, wallet, _ = self.get_or_create_user(discord_user_id, commit=commit)

        # Idempotency check
        existing = self._check_idempotency(
            idempotency_key=idempotency_key,
            user_id=str(user.id),
            expected_type=TransactionType.CREDIT,
            expected_amount=amount,
        )
        if existing:
            return existing

        try:
            wallet = self._lock_wallet_row(wallet.id)
            wallet.available_balance += amount

            ledger_entry = LedgerEntry(
                user_id=user.id,
                amount=amount,
                transaction_type=TransactionType.CREDIT,
                reference_type=reference_type,
                reference_id=reference_id,
                description=description,
                idempotency_key=idempotency_key,
            )
            self.session.add(ledger_entry)
            if commit:
                self.session.commit()
                self.session.refresh(ledger_entry)
            else:
                self.session.flush()
                self.session.refresh(ledger_entry)
            logger.info(
                "Credited %d OBX to user %s (New available: %d)",
                amount,
                discord_user_id,
                wallet.available_balance,
            )
            return ledger_entry
        except IntegrityError:
            self.session.rollback()
            existing = self._check_idempotency(
                idempotency_key=idempotency_key,
                user_id=str(user.id),
                expected_type=TransactionType.CREDIT,
                expected_amount=amount,
            )
            if existing:
                return existing
            raise

    def debit(
        self,
        discord_user_id: str,
        amount: int,
        reference_type: str,
        idempotency_key: str,
        reference_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> LedgerEntry:
        """Debits funds from user's available balance atomically."""
        self._validate_amount(amount)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")

        user = self.get_user(discord_user_id)
        wallet = user.wallet

        # Idempotency check
        existing = self._check_idempotency(
            idempotency_key=idempotency_key,
            user_id=str(user.id),
            expected_type=TransactionType.DEBIT,
            expected_amount=amount,
        )
        if existing:
            return existing

        try:
            wallet = self._lock_wallet_row(wallet.id)
            if wallet.available_balance < amount:
                raise InsufficientFundsError(
                    required=amount,
                    available=wallet.available_balance,
                    fund_type="available",
                )

            wallet.available_balance -= amount

            ledger_entry = LedgerEntry(
                user_id=user.id,
                amount=amount,
                transaction_type=TransactionType.DEBIT,
                reference_type=reference_type,
                reference_id=reference_id,
                description=description,
                idempotency_key=idempotency_key,
            )
            self.session.add(ledger_entry)
            if commit:
                self.session.commit()
                self.session.refresh(ledger_entry)
            else:
                self.session.flush()
                self.session.refresh(ledger_entry)
            logger.info(
                "Debited %d OBX from user %s (New available: %d)",
                amount,
                discord_user_id,
                wallet.available_balance,
            )
            return ledger_entry
        except IntegrityError:
            self.session.rollback()
            existing = self._check_idempotency(
                idempotency_key=idempotency_key,
                user_id=str(user.id),
                expected_type=TransactionType.DEBIT,
                expected_amount=amount,
            )
            if existing:
                return existing
            raise

    def lock_funds(
        self,
        discord_user_id: str,
        amount: int,
        reference_type: str,
        idempotency_key: str,
        reference_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> LedgerEntry:
        """Locks funds by moving from available_balance to locked_balance atomically."""
        self._validate_amount(amount)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")

        user = self.get_user(discord_user_id)
        wallet = user.wallet

        # Idempotency check
        existing = self._check_idempotency(
            idempotency_key=idempotency_key,
            user_id=str(user.id),
            expected_type=TransactionType.LOCK,
            expected_amount=amount,
        )
        if existing:
            return existing

        try:
            wallet = self._lock_wallet_row(wallet.id)
            if wallet.available_balance < amount:
                raise InsufficientFundsError(
                    required=amount,
                    available=wallet.available_balance,
                    fund_type="available",
                )

            wallet.available_balance -= amount
            wallet.locked_balance += amount

            ledger_entry = LedgerEntry(
                user_id=user.id,
                amount=amount,
                transaction_type=TransactionType.LOCK,
                reference_type=reference_type,
                reference_id=reference_id,
                description=description,
                idempotency_key=idempotency_key,
            )
            self.session.add(ledger_entry)
            if commit:
                self.session.commit()
                self.session.refresh(ledger_entry)
            else:
                self.session.flush()
                self.session.refresh(ledger_entry)
            logger.info(
                "Locked %d OBX for user %s (Available: %d, Locked: %d)",
                amount,
                discord_user_id,
                wallet.available_balance,
                wallet.locked_balance,
            )
            return ledger_entry
        except IntegrityError:
            self.session.rollback()
            existing = self._check_idempotency(
                idempotency_key=idempotency_key,
                user_id=str(user.id),
                expected_type=TransactionType.LOCK,
                expected_amount=amount,
            )
            if existing:
                return existing
            raise

    def release_funds(
        self,
        discord_user_id: str,
        amount: int,
        reference_type: str,
        idempotency_key: str,
        reference_id: str | None = None,
        description: str | None = None,
        commit: bool = True,
    ) -> LedgerEntry:
        """Releases funds by moving from locked_balance to available_balance atomically."""
        self._validate_amount(amount)
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string.")

        user = self.get_user(discord_user_id)
        wallet = user.wallet

        # Idempotency check
        existing = self._check_idempotency(
            idempotency_key=idempotency_key,
            user_id=str(user.id),
            expected_type=TransactionType.RELEASE,
            expected_amount=amount,
        )
        if existing:
            return existing

        try:
            wallet = self._lock_wallet_row(wallet.id)
            if wallet.locked_balance < amount:
                raise InsufficientFundsError(
                    required=amount,
                    available=wallet.locked_balance,
                    fund_type="locked",
                )

            wallet.locked_balance -= amount
            wallet.available_balance += amount

            ledger_entry = LedgerEntry(
                user_id=user.id,
                amount=amount,
                transaction_type=TransactionType.RELEASE,
                reference_type=reference_type,
                reference_id=reference_id,
                description=description,
                idempotency_key=idempotency_key,
            )
            self.session.add(ledger_entry)
            if commit:
                self.session.commit()
                self.session.refresh(ledger_entry)
            else:
                self.session.flush()
                self.session.refresh(ledger_entry)
            logger.info(
                "Released %d OBX for user %s (Available: %d, Locked: %d)",
                amount,
                discord_user_id,
                wallet.available_balance,
                wallet.locked_balance,
            )
            return ledger_entry
        except IntegrityError:
            self.session.rollback()
            existing = self._check_idempotency(
                idempotency_key=idempotency_key,
                user_id=str(user.id),
                expected_type=TransactionType.RELEASE,
                expected_amount=amount,
            )
            if existing:
                return existing
            raise

    def get_transactions(
        self,
        discord_user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[LedgerEntry], int]:
        """Retrieves paginated ledger entries for a user."""
        user = self.get_user(discord_user_id)
        
        total = self.session.execute(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.user_id == user.id)
        ).scalar() or 0

        entries = self.session.execute(
            select(LedgerEntry)
            .where(LedgerEntry.user_id == user.id)
            .order_by(LedgerEntry.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).scalars().all()

        return list(entries), total
