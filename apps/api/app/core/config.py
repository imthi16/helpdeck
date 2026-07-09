from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "HelpDeck API"
    version: str = "0.1.0"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://helpdeck:helpdeck@localhost:5433/helpdeck"
    redis_url: str = "redis://localhost:6380/0"

    # Local filesystem storage for raw uploaded sources (object store in prod).
    storage_dir: str = "./storage"

    jwt_secret: str = "change-me"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    llm_cheap_model: str = ""
    llm_strong_model: str = ""
    reranker: str = "none"

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    allowed_origins: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
