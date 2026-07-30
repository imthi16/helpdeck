from collections.abc import AsyncIterator

import pytest

from app.services.llm import (
    LiteLLMProvider,
    LLMError,
    LLMGateway,
    LLMMessage,
    LLMRoute,
    LLMUsage,
    OfflineGroundedProvider,
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


# --- rate-limit retry --------------------------------------------------------
#
# Hosted free tiers meter hard (Gemini caps some models at 20 requests/day), so a
# single 429 must not void a whole eval run. Equally, an exhausted *daily* quota
# must fail fast rather than sleeping for hours.


def test_a_rate_limit_is_retried_with_backoff() -> None:
    delay = LiteLLMProvider._retry_delay(Exception("litellm.RateLimitError: 429"), attempt=0)

    assert delay == LiteLLMProvider.BASE_RETRY_DELAY_SECONDS


def test_backoff_grows_with_each_attempt() -> None:
    first = LiteLLMProvider._retry_delay(Exception("429 RESOURCE_EXHAUSTED"), attempt=0)
    third = LiteLLMProvider._retry_delay(Exception("429 RESOURCE_EXHAUSTED"), attempt=2)

    assert first is not None and third is not None and third > first


def test_the_providers_own_retry_hint_wins_over_our_backoff() -> None:
    """Gemini says "Please retry in 26.8s" — it knows better than our guess."""
    delay = LiteLLMProvider._retry_delay(
        Exception("429 RESOURCE_EXHAUSTED. Please retry in 26.814627312s."), attempt=0
    )

    assert delay == pytest.approx(26.814627312)


def test_an_exhausted_daily_quota_fails_fast_instead_of_sleeping() -> None:
    """A multi-hour retry hint means the daily cap is gone; waiting is pointless."""
    assert (
        LiteLLMProvider._retry_delay(
            Exception("429 RESOURCE_EXHAUSTED. Please retry in 72000s."), attempt=0
        )
        is None
    )


def test_non_rate_limit_errors_are_not_retried() -> None:
    """A 400 or a bad model fails identically next time — retrying only delays it."""
    for message in (
        "litellm.NotFoundError: model is no longer available to new users",
        "litellm.BadRequestError: invalid request",
        "litellm.AuthenticationError: bad key",
    ):
        assert LiteLLMProvider._retry_delay(Exception(message), attempt=0) is None


async def test_complete_retries_a_rate_limit_then_succeeds(monkeypatch) -> None:
    provider = LiteLLMProvider(api_key="k")
    monkeypatch.setattr(LiteLLMProvider, "BASE_RETRY_DELAY_SECONDS", 0.0)
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("litellm.RateLimitError: 429 RESOURCE_EXHAUSTED")
        return "recovered"

    assert await provider._with_rate_limit_retry("m", flaky) == "recovered"
    assert calls["n"] == 2


async def test_complete_gives_up_after_the_retry_budget(monkeypatch) -> None:
    provider = LiteLLMProvider(api_key="k")
    monkeypatch.setattr(LiteLLMProvider, "BASE_RETRY_DELAY_SECONDS", 0.0)
    calls = {"n": 0}

    async def always_limited() -> str:
        calls["n"] += 1
        raise RuntimeError("litellm.RateLimitError: 429 RESOURCE_EXHAUSTED")

    with pytest.raises(RuntimeError):
        await provider._with_rate_limit_retry("m", always_limited)

    assert calls["n"] == LiteLLMProvider.MAX_RATE_LIMIT_RETRIES + 1


async def test_a_non_retryable_error_is_raised_on_the_first_attempt(monkeypatch) -> None:
    provider = LiteLLMProvider(api_key="k")
    calls = {"n": 0}

    async def bad_request() -> str:
        calls["n"] += 1
        raise RuntimeError("litellm.BadRequestError: nope")

    with pytest.raises(RuntimeError):
        await provider._with_rate_limit_retry("m", bad_request)

    assert calls["n"] == 1, "a 400 must not be retried"


# --- hosted provider selection -----------------------------------------------


def test_a_gemini_key_selects_the_litellm_provider(monkeypatch) -> None:
    """Gemini is the only hosted provider that also serves 768-dim embeddings."""
    from app.core.config import get_settings
    from app.services.llm import _default_provider

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_BASE_URL", "")
    get_settings.cache_clear()
    try:
        assert isinstance(_default_provider(), LiteLLMProvider)
    finally:
        get_settings.cache_clear()


def test_upstream_capacity_errors_are_retried() -> None:
    """503 "high demand" is transient — shared capacity, not a bad request."""
    for message in (
        "litellm.ServiceUnavailableError: GeminiException - 503 UNAVAILABLE",
        "This model is currently experiencing high demand",
    ):
        assert LiteLLMProvider._retry_delay(Exception(message), attempt=0) is not None
