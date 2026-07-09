from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.routers import auth, chat, documents, internal
from app.services.queue import create_arq_pool


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Set up shared resources: agent checkpointer and the arq ingest pool."""
    logger = get_logger(__name__)
    settings = get_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        application.state.arq_pool = await create_arq_pool()
        logger.info("arq_pool_ready")
    except Exception as exc:  # noqa: BLE001 - degrade gracefully without queue
        logger.warning("arq_pool_unavailable", error=str(exc))
        application.state.arq_pool = None

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

    pool = getattr(application.state, "arq_pool", None)
    if pool is not None:
        await pool.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.debug)
    logger = get_logger(__name__)

    application = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

    origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    application.include_router(auth.router)
    application.include_router(documents.router)
    # Router is always mounted; each request is gated by ENABLE_INTERNAL_ROUTES.
    application.include_router(internal.router)
    application.include_router(chat.router)

    logger.info("app_created", app_name=settings.app_name, version=settings.version)
    return application


app = create_app()
