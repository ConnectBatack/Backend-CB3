import asyncio

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.services.seed import seed_database


async def _run() -> None:
    settings = get_settings()
    async with AsyncSessionLocal() as db:
        stats = await seed_database(db, include_demo_data=(settings.APP_ENV == "development"))
        print(f"[seed] Terminé : {stats}")


if __name__ == "__main__":
    asyncio.run(_run())

