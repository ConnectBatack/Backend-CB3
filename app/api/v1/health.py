from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db

router = APIRouter(tags=["Santé"])


@router.get("/health", summary="Vérification de disponibilité")
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "service": settings.APP_NAME, "env": settings.APP_ENV}


@router.get("/ready", summary="Vérification base de données et stockage")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    settings = get_settings()
    await db.execute(text("SELECT 1"))
    storage_ok = settings.storage_path.exists()
    return {
        "status": "ready",
        "database": "connected",
        "storage": "ok" if storage_ok else "missing",
    }

