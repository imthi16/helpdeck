"""Embedding gateway: batched, retried, Langfuse-traced.

All embedding calls in the app go through ``EmbeddingService``. Batches are
capped at 100 texts per provider call; transient provider failures are
retried with exponential backoff.
"""

import asyncio
import hashlib
import math
import re
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm import ollama_reachable
from app.services.tracing import get_langfuse

logger = get_logger(__name__)

MAX_BATCH_SIZE = 100
MAX_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 0.5
# Embedding vector width, from the EMBEDDING_DIMS setting (default 768 for
# nomic-embed-text). Must match the chunks.embedding column width — changing it
# requires a matching Alembic migration.
EMBEDDING_DIMS = get_settings().embedding_dims


class EmbeddingError(Exception):
    pass


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str], model: str) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        response = await self._client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]


class OllamaEmbeddingProvider:
    """Local, free embeddings served by Ollama, via the litellm gateway.

    Model names are litellm-style, e.g. ``ollama/nomic-embed-text``. No API key
    or extra SDK — litellm talks to Ollama over HTTP at ``api_base``.
    """

    def __init__(self, api_base: str) -> None:
        self._api_base = api_base

    @staticmethod
    def _index(item: Any) -> int:
        return item["index"] if isinstance(item, dict) else item.index

    @staticmethod
    def _vector(item: Any) -> list[float]:
        return item["embedding"] if isinstance(item, dict) else item.embedding

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        import litellm

        response = await litellm.aembedding(model=model, input=texts, api_base=self._api_base)
        return [self._vector(item) for item in sorted(response.data, key=self._index)]


_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    """a an and are as at be but by can do does for from has have how i if in into is it
    its my no not of on or our so that the their then there these this to up us was we
    what when where which who will with you your""".split()
)


def _normalize_token(token: str) -> str:
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _lexical_tokens(text: str) -> list[str]:
    return [
        _normalize_token(token)
        for token in _TOKEN.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


class HashingEmbeddingProvider:
    """Deterministic, offline bag-of-tokens embedding for dev without an API key.

    Content tokens (stopwords dropped, lightly suffix-normalized) are hashed into
    ``dims`` buckets and the vector is L2-normalized, so cosine similarity
    approximates lexical overlap. Not a substitute for a real semantic model —
    used only when neither Ollama nor an ``OPENAI_API_KEY`` is available.
    """

    def __init__(self, dims: int = EMBEDDING_DIMS) -> None:
        self._dims = dims

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        for token in _lexical_tokens(text):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest, "big") % self._dims
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm > 0:
            vector = [value / norm for value in vector]
        return vector

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


def _default_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.openai_api_key:
        return OpenAIEmbeddingProvider(settings.openai_api_key)
    if ollama_reachable(settings.ollama_base_url):
        logger.info("embeddings_ollama_mode", base_url=settings.ollama_base_url)
        return OllamaEmbeddingProvider(settings.ollama_base_url)
    logger.warning(
        "embeddings_offline_mode",
        reason="no OPENAI_API_KEY and Ollama unreachable; using hashing provider",
    )
    return HashingEmbeddingProvider(EMBEDDING_DIMS)


class EmbeddingService:
    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        *,
        model: str | None = None,
        max_batch_size: int = MAX_BATCH_SIZE,
        max_retries: int = MAX_RETRIES,
        base_retry_delay: float = BASE_RETRY_DELAY_SECONDS,
    ) -> None:
        settings = get_settings()
        self._provider = provider or _default_provider()
        self._model = model or settings.embedding_model
        self._max_batch_size = max_batch_size
        self._max_retries = max_retries
        self._base_retry_delay = base_retry_delay

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._max_batch_size):
            batch = texts[start : start + self._max_batch_size]
            vectors.extend(await self._embed_batch_traced(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_texts([text]))[0]

    async def _embed_batch_traced(self, batch: list[str]) -> list[list[float]]:
        langfuse = get_langfuse()
        if langfuse is None:
            return await self._embed_batch_with_retry(batch)
        with langfuse.start_as_current_generation(
            name="embed_texts",
            model=self._model,
            input={"batch_size": len(batch)},
        ) as generation:
            vectors = await self._embed_batch_with_retry(batch)
            generation.update(output={"vectors": len(vectors)})
            return vectors

    async def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                vectors = await self._provider.embed(batch, self._model)
            except Exception as exc:
                last_error = exc
                if attempt == self._max_retries:
                    break
                delay = self._base_retry_delay * (2**attempt)
                logger.warning(
                    "embedding_retry",
                    attempt=attempt + 1,
                    delay_seconds=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)
                continue
            if len(vectors) != len(batch):
                raise EmbeddingError(
                    f"provider returned {len(vectors)} vectors for {len(batch)} texts"
                )
            return vectors
        raise EmbeddingError(f"embedding failed after {self._max_retries + 1} attempts") from (
            last_error
        )
