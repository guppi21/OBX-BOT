from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from packages.shared.enums import TransactionType


class LedgerEntryResponse(BaseModel):
    id: UUID
    user_id: UUID
    amount: int
    transaction_type: TransactionType
    reference_type: str
    reference_id: str | None
    description: str | None
    idempotency_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionsListResponse(BaseModel):
    discord_user_id: str
    total: int
    limit: int
    offset: int
    transactions: list[LedgerEntryResponse]
