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
from apps.obx_core.api.error_handlers import register_error_handlers
from apps.obx_core.api.routes import health_router, users_router, wallets_router

logger = get_logger("obx.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging()
    logger.info("Initializing OBX Core API...")
    yield
    # Shutdown
    logger.info("Shutting down OBX Core API...")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="OBX Economy Core API",
        version="0.1.0",
        description="Internal API for the OBX Discord Economy Platform (Phase 1)",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS configuration (placeholder for future frontends/services)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register error handlers
    register_error_handlers(app)

    # Include route groups
    app.include_router(health_router)
    app.include_router(users_router)
    app.include_router(wallets_router)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "apps.obx_core.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
    )
