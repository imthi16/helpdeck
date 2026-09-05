import pytest

from app.services.embeddings import EmbeddingError, EmbeddingService


class FakeProvider:
    """Records each batch it is asked to embed; optionally fails N times first."""

    def __init__(self, dims: int = 3, fail_times: int = 0) -> None:
        self.dims = dims
        self.fail_times = fail_times
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("transient provider error")
        return [[float(i)] * self.dims for i in range(len(texts))]


async def test_empty_input_returns_empty() -> None:
    provider = FakeProvider()
    service = EmbeddingService(provider, model="test")
    assert await service.embed_texts([]) == []
    assert provider.calls == []


async def test_batches_at_max_size() -> None:
    provider = FakeProvider()
    service = EmbeddingService(provider, model="test", max_batch_size=100)

    texts = [f"text-{i}" for i in range(250)]
    vectors = await service.embed_texts(texts)

    assert len(vectors) == 250
    assert [len(call) for call in provider.calls] == [100, 100, 50]


async def test_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.services.embeddings.asyncio.sleep", fake_sleep)

    provider = FakeProvider(fail_times=2)
    service = EmbeddingService(provider, model="test", max_retries=3, base_retry_delay=0.5)

    vectors = await service.embed_texts(["a", "b"])

    assert len(vectors) == 2
    assert len(provider.calls) == 3  # 2 failures + 1 success
    assert sleeps == [0.5, 1.0]  # exponential backoff


async def test_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("app.services.embeddings.asyncio.sleep", fake_sleep)

    provider = FakeProvider(fail_times=99)
    service = EmbeddingService(provider, model="test", max_retries=2)

    with pytest.raises(EmbeddingError):
        await service.embed_texts(["a"])

    assert len(provider.calls) == 3  # initial + 2 retries


async def test_embed_query_returns_single_vector() -> None:
    provider = FakeProvider(dims=4)
    service = EmbeddingService(provider, model="test")

    vector = await service.embed_query("hello")

    assert len(vector) == 4
    assert provider.calls == [["hello"]]


# --- Gemini provider ---------------------------------------------------------
#
# Gemini exists here for one reason: it is the only hosted provider that serves a
# 768-dim embedding, so it needs no migration of the chunks.embedding column
# (OpenAI's 1536 would). Its default output is 3072 wide, so the requested width
# is load-bearing — if it were ever dropped, every insert would fail at the DB
# layer with a dimension mismatch far from the cause.


class RecordingAembedding:
    """Stands in for litellm.aembedding, capturing the kwargs it was called with."""

    def __init__(self, dims: int) -> None:
        self.dims = dims
        self.kwargs: dict = {}

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        n = len(kwargs["input"])

        class Response:
            data = [{"index": i, "embedding": [0.1] * self.dims} for i in range(n)]

        return Response()


async def test_gemini_requests_the_pinned_width(monkeypatch) -> None:
    import sys
    import types

    from app.services.embeddings import GeminiEmbeddingProvider

    fake = RecordingAembedding(dims=768)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(aembedding=fake))

    provider = GeminiEmbeddingProvider("key", dims=768)
    vectors = await provider.embed(["a", "b"], "gemini/gemini-embedding-001")

    assert fake.kwargs["dimensions"] == 768, "the requested width must be sent"
    assert fake.kwargs["api_key"] == "key"
    assert [len(v) for v in vectors] == [768, 768]


async def test_gemini_rejects_a_width_that_would_not_fit_the_column(monkeypatch) -> None:
    """Fail at the provider, not on the INSERT — pgvector's error names no cause."""
    import sys
    import types

    from app.services.embeddings import GeminiEmbeddingProvider

    monkeypatch.setitem(
        sys.modules, "litellm", types.SimpleNamespace(aembedding=RecordingAembedding(dims=3072))
    )

    provider = GeminiEmbeddingProvider("key", dims=768)
    with pytest.raises(EmbeddingError, match="3072-dim vectors, expected 768"):
        await provider.embed(["a"], "gemini/gemini-embedding-001")


def test_a_gemini_key_selects_the_gemini_embedder(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.services.embeddings import GeminiEmbeddingProvider, _default_provider

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    get_settings.cache_clear()
    try:
        assert isinstance(_default_provider(), GeminiEmbeddingProvider)
    finally:
        get_settings.cache_clear()


async def test_embedding_calls_carry_a_timeout(monkeypatch) -> None:
    """A provider that accepts a request and never answers would otherwise block the
    batch forever — the failure mode that cost a 30-item eval run 7h16m
    (docs/NOTES.md, Phase 6.4). Both litellm-backed providers must set a bound."""
    import sys
    import types

    from app.services.embeddings import (
        EMBEDDING_TIMEOUT_SECONDS,
        GeminiEmbeddingProvider,
        OllamaEmbeddingProvider,
    )

    fake = RecordingAembedding(dims=768)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(aembedding=fake))

    await GeminiEmbeddingProvider("key", dims=768).embed(["a"], "gemini/gemini-embedding-001")
    assert fake.kwargs["timeout"] == EMBEDDING_TIMEOUT_SECONDS

    await OllamaEmbeddingProvider("http://localhost:11434").embed(["a"], "ollama/nomic-embed-text")
    assert fake.kwargs["timeout"] == EMBEDDING_TIMEOUT_SECONDS


#: A wedged embedding batch must fail inside single-digit minutes. Deliberately a
#: literal and not LLM_TIMEOUT_SECONDS: that setting is meant to be retuned per host
#: (60s hosted, 600s on CPU Ollama), so anchoring an invariant to it makes the
#: invariant move with the deployment — this assertion is about the embedding path's
#: own retry arithmetic, which does not.
MAX_WEDGED_BATCH_SECONDS = 600.0


def test_the_embedding_bound_stays_well_under_the_retry_budget() -> None:
    """The bound is deliberately not LLM_TIMEOUT_SECONDS: _embed_batch_with_retry
    retries any exception MAX_RETRIES times, so a 600s bound would cap a wedged batch
    at ~40 minutes — worse than the wedge it replaces."""
    from app.services.embeddings import EMBEDDING_TIMEOUT_SECONDS, MAX_RETRIES

    worst_case = EMBEDDING_TIMEOUT_SECONDS * (MAX_RETRIES + 1)
    assert worst_case <= MAX_WEDGED_BATCH_SECONDS


def test_the_chat_gateway_does_not_capture_embeddings(monkeypatch) -> None:
    """LLM_API_BASE selects chat only. These gateways serve text-embedding-3-small
    at 1536 dims and chunks.embedding is 768 wide, so letting one capture embeddings
    would need a migration and a full re-seed — the hybrid keeps Gemini here."""
    from app.core.config import get_settings
    from app.services.embeddings import GeminiEmbeddingProvider, _default_provider

    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("LLM_API_BASE", "https://gateway.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-gateway")
    get_settings.cache_clear()
    try:
        assert isinstance(_default_provider(), GeminiEmbeddingProvider)
    finally:
        get_settings.cache_clear()
