from apps.obx_core.api.routes.health import router as health_router
from apps.obx_core.api.routes.users import router as users_router
from apps.obx_core.api.routes.wallets import router as wallets_router

__all__ = ["health_router", "users_router", "wallets_router"]
