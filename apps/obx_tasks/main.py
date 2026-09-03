import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from packages.shared.config import get_settings
from packages.shared.logging import setup_logging, get_logger
from apps.obx_tasks.api.error_handlers import register_task_error_handlers
from apps.obx_tasks.api.routes import health_router, tasks_router, submissions_router

logger = get_logger("obx.tasks.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("Initializing OBX Social Tasks API...")
    yield
    logger.info("Shutting down OBX Social Tasks API...")


def create_task_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="OBX Social Tasks API",
        version="0.1.0",
        description="Task creation, submission, and verification engine for the OBX Discord Economy (Phase 2A)",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_task_error_handlers(app)

    app.include_router(health_router)
    app.include_router(tasks_router)
    app.include_router(submissions_router)

    return app


app = create_task_app()

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "apps.obx_tasks.main:app",
        host=settings.API_HOST,
        port=settings.TASK_SERVICE_PORT,
        reload=True,
    )
