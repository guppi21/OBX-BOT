from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from packages.shared.exceptions import (
    TaskError,
    TaskNotFoundError,
    TaskNotActiveError,
    TaskExpiredError,
    InvalidRewardPoolError,
    RewardPoolExhaustedError,
    SubmissionNotFoundError,
    DuplicateSubmissionError,
    InvalidSubmissionStatusError,
    UnauthorizedAdminError,
)
from packages.shared.logging import get_logger

logger = get_logger("obx.tasks.errors")


def register_task_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(TaskNotFoundError)
    @app.exception_handler(SubmissionNotFoundError)
    async def not_found_handler(request: Request, exc: TaskError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(DuplicateSubmissionError)
    async def duplicate_submission_handler(request: Request, exc: DuplicateSubmissionError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": exc.message,
                "code": exc.code,
                "details": {"task_id": exc.task_id, "discord_user_id": exc.discord_user_id},
            },
        )

    @app.exception_handler(TaskNotActiveError)
    @app.exception_handler(TaskExpiredError)
    @app.exception_handler(InvalidRewardPoolError)
    @app.exception_handler(RewardPoolExhaustedError)
    @app.exception_handler(InvalidSubmissionStatusError)
    async def task_bad_request_handler(request: Request, exc: TaskError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(UnauthorizedAdminError)
    async def unauthorized_handler(request: Request, exc: UnauthorizedAdminError):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(TaskError)
    async def task_base_handler(request: Request, exc: TaskError):
        logger.error("Task domain error: %s (%s)", exc.message, exc.code)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": exc.message, "code": exc.code},
        )
