from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.debug)
    logger = get_logger(__name__)

    application = FastAPI(title=settings.app_name, version=settings.version)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": settings.version}

    logger.info("app_created", app_name=settings.app_name, version=settings.version)
    return application


app = create_app()
