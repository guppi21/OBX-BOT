from apps.obx_tasks.api.routes.health import router as health_router
from apps.obx_tasks.api.routes.tasks import router as tasks_router
from apps.obx_tasks.api.routes.submissions import router as submissions_router

__all__ = ["health_router", "tasks_router", "submissions_router"]
