from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "HelpDeck API"
    version: str = "0.1.0"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://helpdeck:helpdeck@localhost:5433/helpdeck"
    # Non-superuser role the app serves requests as; RLS is enforced against it.
    # Migrations/seed/tests use database_url (superuser, bypasses RLS).
    app_database_url: str = "postgresql+asyncpg://helpdeck_app:helpdeck_app@localhost:5433/helpdeck"
    redis_url: str = "redis://localhost:6380/0"

    # Local filesystem storage for raw uploaded sources (object store in prod).
    storage_dir: str = "./storage"

    # Dev-only internal routes (e.g. /internal/search). Never enable in prod.
    enable_internal_routes: bool = False

    # Agent thresholds
    # Escalate a grounded answer only when the faithfulness judge is quite unsure.
    # Tuned for local OSS judge models (llama3.2/qwen2.5), which under-score terse
    # but correct grounded answers; 0.7 caused frequent false escalations. Out-of-KB
    # questions still escalate via the no-grounding path, independent of this value.
    # Raise toward 0.7 when using a stronger hosted judge model.
    faithfulness_threshold: float = 0.4
    agent_retrieval_top_n: int = 8

    # Response cache
    response_cache_ttl_seconds: int = 3600

    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    cookie_secure: bool = False
    cookie_domain: str | None = None

    # LLM/embeddings default to a free, local, open-source stack served by Ollama
    # (reached through the litellm gateway). Set ANTHROPIC/OPENAI keys and matching
    # *_MODEL / EMBEDDING_MODEL values to use a hosted provider instead. When
    # neither a key nor a reachable Ollama is present, deterministic offline stubs
    # keep the app runnable (not real models).
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Gemini is the one hosted provider that serves both chat and a 768-dim
    # embedding model (gemini-embedding-001 with outputDimensionality=768), so it
    # can back the whole pipeline without touching the chunks.embedding width.
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    embedding_model: str = "ollama/nomic-embed-text"
    # Must match EMBEDDING_MODEL's output width (nomic-embed-text = 768).
    embedding_dims: int = 768
    # Cheap route (router/chitchat) stays small; the strong route (grounded answer
    # + faithfulness judge) uses a larger model for better answers and fewer false
    # escalations. Both are pulled by the compose ollama-pull service.
    llm_cheap_model: str = "ollama_chat/llama3.2:3b"
    llm_strong_model: str = "ollama_chat/qwen2.5:7b"
    # Hard upper bound on ONE model call. Nothing below this line reliably sets one:
    # on 2026-08-01 Ollama evicted a model mid-request and the client blocked forever,
    # costing a 30-item eval run 7h16m and producing nothing (docs/NOTES.md, 6.4).
    # litellm nominally defaults to 600s and did not fire, so the bound has to be one
    # we set and can assert on. 600s is ~1.5x the slowest healthy call measured on
    # CPU-only Ollama (qwen2.5:7b at 410s, already under concurrency-4 contention);
    # hosted providers answer in seconds. Raise it on a slower box, don't remove it.
    llm_timeout_seconds: float = 600.0
    reranker: str = "none"
    cohere_api_key: str = ""

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    allowed_origins: str = "http://localhost:3000"

    # Dashboard base URL, used to build invite links (no mailer yet: the link is
    # returned to the inviter and logged).
    web_app_url: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
