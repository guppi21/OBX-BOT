class OBXError(Exception):
    """Base exception for all OBX domain errors."""

    def __init__(self, message: str, code: str = "OBX_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class UserNotFoundError(OBXError):
    def __init__(self, discord_user_id: str):
        super().__init__(
            f"User with Discord ID '{discord_user_id}' not found.",
            code="USER_NOT_FOUND",
        )
        self.discord_user_id = discord_user_id


class DuplicateUserError(OBXError):
    def __init__(self, discord_user_id: str):
        super().__init__(
            f"User with Discord ID '{discord_user_id}' already exists.",
            code="DUPLICATE_USER",
        )
        self.discord_user_id = discord_user_id


class WalletNotFoundError(OBXError):
    def __init__(self, user_id_or_discord_id: str):
        super().__init__(
            f"Wallet for '{user_id_or_discord_id}' not found.",
            code="WALLET_NOT_FOUND",
        )


class DuplicateWalletError(OBXError):
    def __init__(self, user_id: str):
        super().__init__(
            f"Wallet already exists for user '{user_id}'.",
            code="DUPLICATE_WALLET",
        )


class InsufficientFundsError(OBXError):
    def __init__(
        self,
        required: int,
        available: int,
        fund_type: str = "available",
    ):
        super().__init__(
            f"Insufficient {fund_type} funds: required {required}, but currently has {available}.",
            code="INSUFFICIENT_FUNDS",
        )
        self.required = required
        self.available = available
        self.fund_type = fund_type


class InvalidAmountError(OBXError):
    def __init__(self, amount: int):
        super().__init__(
            f"Invalid transaction amount: {amount}. Amount must be a positive integer (> 0).",
            code="INVALID_AMOUNT",
        )
        self.amount = amount


class IdempotencyConflictError(OBXError):
    def __init__(self, idempotency_key: str, reason: str = "Conflicting parameters"):
        super().__init__(
            f"Idempotency key '{idempotency_key}' was previously used with different parameters: {reason}.",
            code="IDEMPOTENCY_CONFLICT",
        )
        self.idempotency_key = idempotency_key


# Task Domain Exceptions
class TaskError(OBXError):
    """Base exception for task system errors."""
    pass


class TaskNotFoundError(TaskError):
    def __init__(self, task_id: str):
        super().__init__(
            f"Task '{task_id}' not found.",
            code="TASK_NOT_FOUND",
        )
        self.task_id = task_id


class TaskNotActiveError(TaskError):
    def __init__(self, task_id: str, status: str):
        super().__init__(
            f"Task '{task_id}' is not active (current status: {status}).",
            code="TASK_NOT_ACTIVE",
        )
        self.task_id = task_id
        self.status = status


class TaskExpiredError(TaskError):
    def __init__(self, task_id: str):
        super().__init__(
            f"Task '{task_id}' has expired.",
            code="TASK_EXPIRED",
        )
        self.task_id = task_id


class InvalidRewardPoolError(TaskError):
    def __init__(self, message: str):
        super().__init__(message, code="INVALID_REWARD_POOL")


class RewardPoolExhaustedError(TaskError):
    def __init__(self, task_id: str, remaining: int, required: int):
        super().__init__(
            f"Task '{task_id}' reward pool exhausted: {remaining} OBX remaining, requires {required} OBX.",
            code="REWARD_POOL_EXHAUSTED",
        )
        self.task_id = task_id
        self.remaining = remaining
        self.required = required


class SubmissionNotFoundError(TaskError):
    def __init__(self, submission_id: str):
        super().__init__(
            f"Task submission '{submission_id}' not found.",
            code="SUBMISSION_NOT_FOUND",
        )
        self.submission_id = submission_id


class DuplicateSubmissionError(TaskError):
    def __init__(self, task_id: str, discord_user_id: str):
        super().__init__(
            f"User '{discord_user_id}' has already submitted proof for task '{task_id}'.",
            code="DUPLICATE_SUBMISSION",
        )
        self.task_id = task_id
        self.discord_user_id = discord_user_id


class InvalidSubmissionStatusError(TaskError):
    def __init__(self, submission_id: str, current_status: str, action: str):
        super().__init__(
            f"Cannot {action} submission '{submission_id}' in '{current_status}' status. Only PENDING submissions can be reviewed.",
            code="INVALID_SUBMISSION_STATUS",
        )
        self.submission_id = submission_id
        self.current_status = current_status
        self.action = action


class UnauthorizedAdminError(TaskError):
    def __init__(self, message: str = "Admin authorization required."):
        super().__init__(message, code="UNAUTHORIZED_ADMIN")
