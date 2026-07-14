import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import get_settings


@pytest.fixture(scope="session")
def migrated_db() -> None:
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
async def db_engine(migrated_db: None) -> AsyncEngine:
    engine = create_async_engine(get_settings().database_url, poolclass=None)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_login(db_engine: AsyncEngine) -> None:
    """Provision the app role's LOGIN + password for the test environment.

    The RLS migration deliberately no longer handles credentials (so no secret
    is ever emitted into migration SQL); deployment secret management does this
    in prod, docker-compose init does it in dev, and this fixture does it for
    tests. Runs as the superuser and is idempotent.
    """
    password = make_url(get_settings().app_database_url).password or "helpdeck_app"
    escaped = password.replace("'", "''")
    async with db_engine.begin() as conn:
        await conn.execute(text(f"ALTER ROLE helpdeck_app LOGIN PASSWORD '{escaped}'"))


@pytest.fixture
def db_sessionmaker(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)
