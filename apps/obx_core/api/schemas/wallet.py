from datetime import datetime
from pydantic import BaseModel, Field


class BalanceResponse(BaseModel):
    discord_user_id: str
    available_balance: int = Field(..., ge=0, description="Available OBX tokens (integer)")
    locked_balance: int = Field(..., ge=0, description="Locked OBX tokens (integer)")
    total_balance: int = Field(..., ge=0, description="Total OBX balance (available + locked)")
    updated_at: datetime


class BaseWalletOperationRequest(BaseModel):
    discord_user_id: str = Field(..., min_length=1, max_length=64, description="Discord Snowflake User ID")
    amount: int = Field(..., gt=0, description="Amount of OBX (positive integer)")
    reference_type: str = Field(..., min_length=1, max_length=64, description="Business reference type")
    idempotency_key: str = Field(..., min_length=1, max_length=128, description="Unique idempotency key")
    reference_id: str | None = Field(default=None, max_length=255, description="Optional external entity reference ID")
    description: str | None = Field(default=None, max_length=500, description="Optional human-readable description")


class CreditRequest(BaseWalletOperationRequest):
    pass


class DebitRequest(BaseWalletOperationRequest):
    pass


class LockFundsRequest(BaseWalletOperationRequest):
    pass


class ReleaseFundsRequest(BaseWalletOperationRequest):
    pass
