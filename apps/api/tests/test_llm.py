import time
from collections.abc import AsyncIterator, Iterator

import pytest

from app.services import llm as llm_module
from app.services.llm import (
    LLMError,
    LLMGateway,
    LLMMessage,
    LLMRoute,
    LLMUsage,
    OfflineGroundedProvider,
    ollama_reachable,
)


class RecordingProvider:
    def __init__(self, text: str = "hello", usage: LLMUsage | None = None) -> None:
        self.text = text
        self.usage = usage or LLMUsage(prompt_tokens=10, completion_tokens=3)
        self.models: list[str] = []

    async def complete(self, model, messages, **kwargs):
        self.models.append(model)
        return self.text, self.usage

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        self.models.append(model)
        for token in self.text.split():
            yield token + " "


class BoomProvider:
    async def complete(self, model, messages, **kwargs):
        raise RuntimeError("provider exploded")

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        raise RuntimeError("provider exploded")
        yield ""  # pragma: no cover


def messages() -> list[LLMMessage]:
    return [LLMMessage("system", "s"), LLMMessage("user", "u")]


async def test_route_selects_configured_models() -> None:
    provider = RecordingProvider()
    gateway = LLMGateway(provider, cheap_model="cheap-1", strong_model="strong-1")

    await gateway.complete(messages(), route=LLMRoute.cheap)
    await gateway.complete(messages(), route=LLMRoute.strong)

    assert provider.models == ["cheap-1", "strong-1"]


async def test_complete_captures_usage_and_latency() -> None:
    provider = RecordingProvider(text="answer", usage=LLMUsage(7, 5))
    gateway = LLMGateway(provider, cheap_model="c", strong_model="s")

    response = await gateway.complete(messages(), route=LLMRoute.strong)

    assert response.text == "answer"
    assert response.model == "s"
    assert response.route == LLMRoute.strong
    assert response.usage.prompt_tokens == 7
    assert response.usage.completion_tokens == 5
    assert response.usage.total_tokens == 12
    assert response.latency_ms >= 0


async def test_stream_yields_pieces() -> None:
    provider = RecordingProvider(text="one two three")
    gateway = LLMGateway(provider, cheap_model="c", strong_model="s")

    pieces = [p async for p in gateway.stream(messages(), route=LLMRoute.cheap)]

    assert "".join(pieces).split() == ["one", "two", "three"]


async def test_provider_error_wrapped() -> None:
    gateway = LLMGateway(BoomProvider(), cheap_model="c", strong_model="s")
    with pytest.raises(LLMError):
        await gateway.complete(messages())


# --- Offline provider behavior (deterministic dev fallback) ------------------


async def test_offline_router_classifies() -> None:
    provider = OfflineGroundedProvider()
    system = LLMMessage("system", "Classify the user request into one label.")

    faq, _ = await provider.complete(
        "m", [system, LLMMessage("user", "what is your refund policy?")]
    )
    human, _ = await provider.complete(
        "m", [system, LLMMessage("user", "I want to talk to a human")]
    )
    chit, _ = await provider.complete("m", [system, LLMMessage("user", "hello there")])

    assert faq == "faq"
    assert human == "human_request"
    assert chit == "chitchat"


async def test_offline_grounded_answer_cites_context() -> None:
    provider = OfflineGroundedProvider()
    user = LLMMessage(
        "user",
        "Context:\n[1] Returns are accepted within 30 days. Extra sentence.\n\nQuestion: returns?",
    )
    text, _ = await provider.complete("m", [LLMMessage("system", "grounded"), user])

    assert "[1]" in text
    assert "30 days" in text


async def test_offline_grounded_answer_refuses_without_context() -> None:
    provider = OfflineGroundedProvider()
    text, _ = await provider.complete(
        "m", [LLMMessage("system", "grounded"), LLMMessage("user", "no context here")]
    )
    assert "don't have enough information" in text


class _FakeConnection:
    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def probe_url() -> Iterator[str]:
    url = "http://ollama-probe-test:11434"
    llm_module._probe_cache.pop(url, None)
    yield url
    llm_module._probe_cache.pop(url, None)


def test_ollama_probe_failure_cached_within_ttl(monkeypatch, probe_url) -> None:
    attempts: list[str] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append("probe")
        raise OSError("connection refused")

    monkeypatch.setattr(llm_module.socket, "create_connection", refuse)

    assert ollama_reachable(probe_url) is False
    assert ollama_reachable(probe_url) is False
    assert attempts == ["probe"]


def test_ollama_probe_failure_retried_after_ttl(monkeypatch, probe_url) -> None:
    monkeypatch.setattr(
        llm_module.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    assert ollama_reachable(probe_url) is False

    # Expire the failure entry, then bring "Ollama" up: the probe must run again.
    llm_module._probe_cache[probe_url] = (False, time.monotonic() - 1)
    monkeypatch.setattr(llm_module.socket, "create_connection", lambda *a, **k: _FakeConnection())

    assert ollama_reachable(probe_url) is True


def test_ollama_probe_success_cached(monkeypatch, probe_url) -> None:
    attempts: list[str] = []

    def accept(*args: object, **kwargs: object) -> _FakeConnection:
        attempts.append("probe")
        return _FakeConnection()

    monkeypatch.setattr(llm_module.socket, "create_connection", accept)

    assert ollama_reachable(probe_url) is True
    assert ollama_reachable(probe_url) is True
    assert attempts == ["probe"]
