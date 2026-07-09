"""Embedding gateway: batched, retried, Langfuse-traced.

All embedding calls in the app go through ``EmbeddingService``. Batches are
capped at 100 texts per provider call; transient provider failures are
retried with exponential backoff.
"""

import asyncio
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.tracing import get_langfuse

logger = get_logger(__name__)

MAX_BATCH_SIZE = 100
MAX_RETRIES = 3
BASE_RETRY_DELAY_SECONDS = 0.5


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
        self._provider = provider or OpenAIEmbeddingProvider(settings.openai_api_key)
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
