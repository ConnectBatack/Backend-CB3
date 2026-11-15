import os
import tempfile
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Configurer l'environnement de test avant d'importer app.*
_TEST_STORAGE = tempfile.mkdtemp(prefix="batack_test_storage_")
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["STORAGE_DIR"] = _TEST_STORAGE
os.environ["SECRET_KEY"] = "test_secret_key_for_pytest_minimum_32_characters_long_2026"
os.environ["SEED_ON_STARTUP"] = "false"

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.core.rate_limit import rate_limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402

get_settings.cache_clear()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    rate_limiter.reset()
    app = create_app()

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def staff_users(db_session: AsyncSession) -> dict[str, User]:
    users = {
        "admin": User(
            email="admin@batack.org",
            full_name="Admin Test",
            role=UserRole.ADMIN,
            password_hash=hash_password("AdminPass2026!"),
            is_active=True,
        ),
        "tresorier": User(
            email="tresorier@batack.org",
            full_name="Trésorier Test",
            role=UserRole.TRESORIER,
            password_hash=hash_password("TresorierPass2026!"),
            is_active=True,
        ),
        "membre": User(
            email="membre@batack.org",
            full_name="Membre Test",
            role=UserRole.MEMBRE,
            password_hash=hash_password("MembrePass2026!"),
            is_active=True,
        ),
        "encadrant": User(
            email="encadrant@batack.org",
            full_name="Encadrant Test",
            role=UserRole.ENCADRANT,
            password_hash=hash_password("EncadrantPass2026!"),
            is_active=True,
        ),
    }
    for u in users.values():
        db_session.add(u)
    await db_session.commit()
    return users


async def login_as(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}

