from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.storage import LocalStorageManager
from app.services.seed import seed_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    LocalStorageManager().ensure_directories()
    if settings.SEED_ON_STARTUP and settings.APP_ENV != "test":
        async with AsyncSessionLocal() as db:
            await seed_database(db, include_demo_data=(settings.APP_ENV == "development"))
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        description=(
            "API REST complète de la plateforme ConnectBatack 3 : dons Stripe, transparence, "
            "registre comptable chaîné, gestion des rôles COVIDEBA/ENSPY/UMONS, AHP, "
            "planification MILP v5_5 et gestion locale des médias."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next) -> Response:
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    return app


app = create_app()

