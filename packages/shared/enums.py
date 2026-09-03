from enum import Enum


class TransactionType(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    LOCK = "LOCK"
    RELEASE = "RELEASE"
    SETTLEMENT = "SETTLEMENT"
    REFUND = "REFUND"


class ReferenceType(str, Enum):
    MANUAL = "manual"
    ADMIN = "admin"
    TRANSFER = "transfer"
    TASK_REWARD = "task_reward"
    AUCTION_BID = "auction_bid"
    AUCTION_WIN = "auction_win"
    AUCTION_REFUND = "auction_refund"
    AUCTION_FCFS = "auction_fcfs"
    SYSTEM = "system"


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    RETWEET = "RETWEET"
    COMMENT = "COMMENT"
    LIKE = "LIKE"
    FOLLOW = "FOLLOW"
    JOIN_DISCORD = "JOIN_DISCORD"
    CUSTOM_TASK = "CUSTOM_TASK"
    MULTI_ACTION = "MULTI_ACTION"


class TaskPlatform(str, Enum):
    X = "X"
    DISCORD = "DISCORD"
    CUSTOM = "CUSTOM"


class SubmissionStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AuctionType(str, Enum):
    FCFS = "FCFS"
    GTD = "GTD"


class AuctionStatus(str, Enum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    SETTLING = "SETTLING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"
