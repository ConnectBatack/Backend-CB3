from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Classe de base pour tous les modèles SQLAlchemy 2.0."""


settings = get_settings()

engine_kwargs: dict = {
    "echo": settings.DEBUG and settings.APP_ENV == "development",
    "pool_pre_ping": True,
}
if settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Fournit une session SQLAlchemy asynchrone par requête."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

