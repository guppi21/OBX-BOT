from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from packages.database.session import get_db
from apps.obx_core.services.wallet_service import WalletService
from apps.obx_core.api.schemas import (
    BalanceResponse,
    TransactionsListResponse,
    LedgerEntryResponse,
    UserResponse,
    CreateUserRequest,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_200_OK)
def get_or_create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
):
    """Safely gets or creates a user and initialized wallet."""
    service = WalletService(db)
    user, _, _ = service.get_or_create_user(request.discord_user_id)
    return user


@router.get("/{discord_user_id}/balance", response_model=BalanceResponse, status_code=status.HTTP_200_OK)
def get_user_balance(
    discord_user_id: str,
    db: Session = Depends(get_db),
):
    """Retrieves available, locked, and total OBX balance for a Discord user."""
    service = WalletService(db)
    balance_data = service.get_balance(discord_user_id)
    return BalanceResponse(**balance_data)


@router.get("/{discord_user_id}/transactions", response_model=TransactionsListResponse, status_code=status.HTTP_200_OK)
def get_user_transactions(
    discord_user_id: str,
    limit: int = Query(50, ge=1, le=500, description="Max entries to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
):
    """Retrieves paginated ledger entries for a Discord user."""
    service = WalletService(db)
    entries, total = service.get_transactions(discord_user_id, limit=limit, offset=offset)
    return TransactionsListResponse(
        discord_user_id=discord_user_id,
        total=total,
        limit=limit,
        offset=offset,
        transactions=[LedgerEntryResponse.model_validate(e) for e in entries],
    )
