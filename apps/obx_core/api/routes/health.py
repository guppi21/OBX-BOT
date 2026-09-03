from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from packages.database.session import get_db
from packages.shared.config import get_settings
from apps.obx_core.api.schemas import HealthResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def health_check(db: Session = Depends(get_db)):
    settings = get_settings()
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"unhealthy: {str(exc)}"

    return HealthResponse(
        status="ok" if db_status == "connected" else "degraded",
        environment=settings.ENVIRONMENT,
        database=db_status,
    )
