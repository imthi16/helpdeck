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
