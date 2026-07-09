from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.routers import chat, internal


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Set up a shared Postgres checkpointer for the agent, if reachable."""
    logger = get_logger(__name__)
    settings = get_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
            await saver.setup()
            application.state.chat_checkpointer = saver
            logger.info("checkpointer_ready")
            yield
    except Exception as exc:  # noqa: BLE001 - degrade gracefully without checkpointer
        logger.warning("checkpointer_unavailable", error=str(exc))
        application.state.chat_checkpointer = None
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.debug)
    logger = get_logger(__name__)

    application = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    # Router is always mounted; each request is gated by ENABLE_INTERNAL_ROUTES.
    application.include_router(internal.router)
    application.include_router(chat.router)

    logger.info("app_created", app_name=settings.app_name, version=settings.version)
    return application


app = create_app()
