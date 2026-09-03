from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from packages.shared.exceptions import (
    OBXError,
    UserNotFoundError,
    DuplicateUserError,
    WalletNotFoundError,
    DuplicateWalletError,
    InsufficientFundsError,
    InvalidAmountError,
    IdempotencyConflictError,
)
from packages.shared.logging import get_logger

logger = get_logger("obx.api.errors")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UserNotFoundError)
    async def user_not_found_handler(request: Request, exc: UserNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": exc.message, "code": exc.code, "details": {"discord_user_id": exc.discord_user_id}},
        )

    @app.exception_handler(WalletNotFoundError)
    async def wallet_not_found_handler(request: Request, exc: WalletNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(DuplicateUserError)
    @app.exception_handler(DuplicateWalletError)
    async def duplicate_entity_handler(request: Request, exc: OBXError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(request: Request, exc: IdempotencyConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": exc.message, "code": exc.code, "details": {"idempotency_key": exc.idempotency_key}},
        )

    @app.exception_handler(InsufficientFundsError)
    async def insufficient_funds_handler(request: Request, exc: InsufficientFundsError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": exc.message,
                "code": exc.code,
                "details": {
                    "required": exc.required,
                    "available": exc.available,
                    "fund_type": exc.fund_type,
                },
            },
        )

    @app.exception_handler(InvalidAmountError)
    async def invalid_amount_handler(request: Request, exc: InvalidAmountError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": exc.message, "code": exc.code, "details": {"amount": exc.amount}},
        )

    @app.exception_handler(OBXError)
    async def obx_base_error_handler(request: Request, exc: OBXError):
        logger.error("Domain exception: %s (%s)", exc.message, exc.code)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": exc.message, "code": exc.code},
        )
