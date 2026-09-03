from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from packages.database.session import get_db
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.api.schemas import (
    CreditRequest,
    DebitRequest,
    LockFundsRequest,
    ReleaseFundsRequest,
    LedgerEntryResponse,
)

router = APIRouter(prefix="/wallets", tags=["Wallets"])


@router.post("/credit", response_model=LedgerEntryResponse, status_code=status.HTTP_200_OK)
def credit_wallet(
    request: CreditRequest,
    db: Session = Depends(get_db),
):
    """Credits OBX tokens to a user's wallet available balance idempotently."""
    service = WalletService(db)
    entry = service.credit(
        discord_user_id=request.discord_user_id,
        amount=request.amount,
        reference_type=request.reference_type,
        idempotency_key=request.idempotency_key,
        reference_id=request.reference_id,
        description=request.description,
    )
    return LedgerEntryResponse.model_validate(entry)


@router.post("/debit", response_model=LedgerEntryResponse, status_code=status.HTTP_200_OK)
def debit_wallet(
    request: DebitRequest,
    db: Session = Depends(get_db),
):
    """Debits OBX tokens from a user's wallet available balance idempotently."""
    service = WalletService(db)
    entry = service.debit(
        discord_user_id=request.discord_user_id,
        amount=request.amount,
        reference_type=request.reference_type,
        idempotency_key=request.idempotency_key,
        reference_id=request.reference_id,
        description=request.description,
    )
    return LedgerEntryResponse.model_validate(entry)


@router.post("/lock", response_model=LedgerEntryResponse, status_code=status.HTTP_200_OK)
def lock_wallet_funds(
    request: LockFundsRequest,
    db: Session = Depends(get_db),
):
    """Locks OBX tokens (moves from available to locked balance) idempotently."""
    service = WalletService(db)
    entry = service.lock_funds(
        discord_user_id=request.discord_user_id,
        amount=request.amount,
        reference_type=request.reference_type,
        idempotency_key=request.idempotency_key,
        reference_id=request.reference_id,
        description=request.description,
    )
    return LedgerEntryResponse.model_validate(entry)


@router.post("/release", response_model=LedgerEntryResponse, status_code=status.HTTP_200_OK)
def release_wallet_funds(
    request: ReleaseFundsRequest,
    db: Session = Depends(get_db),
):
    """Releases locked OBX tokens (moves from locked to available balance) idempotently."""
    service = WalletService(db)
    entry = service.release_funds(
        discord_user_id=request.discord_user_id,
        amount=request.amount,
        reference_type=request.reference_type,
        idempotency_key=request.idempotency_key,
        reference_id=request.reference_id,
        description=request.description,
    )
    return LedgerEntryResponse.model_validate(entry)
