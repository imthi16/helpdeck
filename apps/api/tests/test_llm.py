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


# --- call bounds -------------------------------------------------------------
#
# Nothing in the path reliably bounded a model call before this: litellm nominally
# defaults to 600s and, on 2026-08-01, did not fire — Ollama evicted a model
# mid-request and the client blocked forever, costing a 30-item eval run 7h16m and
# producing nothing (docs/NOTES.md, Phase 6.4). These pin the bounds we set ourselves.


class _RecordingAcompletion:
    """Stands in for ``litellm.acompletion``, capturing the kwargs it was given."""

    def __init__(self, result: object) -> None:
        self.kwargs: dict = {}
        self._result = result

    async def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self._result


def _install_litellm(monkeypatch, acompletion) -> None:
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=acompletion))


def _completion(text: str = "ok"):
    from types import SimpleNamespace

    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


async def test_every_completion_carries_a_request_timeout(monkeypatch) -> None:
    fake = _RecordingAcompletion(_completion())
    _install_litellm(monkeypatch, fake)

    await LiteLLMProvider(api_key="k", timeout=42.0).complete("m", [LLMMessage("user", "hi")])

    assert fake.kwargs["timeout"] == 42.0


async def test_an_explicit_timeout_from_a_call_site_wins(monkeypatch) -> None:
    """`timeout` is placed before **kwargs in _kwargs precisely so this holds."""
    fake = _RecordingAcompletion(_completion())
    _install_litellm(monkeypatch, fake)

    await LiteLLMProvider(api_key="k", timeout=42.0).complete(
        "m", [LLMMessage("user", "hi")], timeout=5.0
    )

    assert fake.kwargs["timeout"] == 5.0


async def test_a_stalled_stream_raises_instead_of_hanging_forever(monkeypatch) -> None:
    """The incident, in miniature: the server accepts the request, sends one chunk,
    then never sends another. The request timeout cannot see this — the HTTP call
    already succeeded — so the gap between chunks needs its own deadline."""
    import asyncio
    from types import SimpleNamespace

    def chunk(text: str):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

    class StallingStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            if not getattr(self, "_sent", False):
                self._sent = True
                return chunk("first ")
            await asyncio.Event().wait()  # never resolves

    async def acompletion(**kwargs):
        return StallingStream()

    _install_litellm(monkeypatch, acompletion)

    pieces: list[str] = []
    with pytest.raises(TimeoutError):
        async for piece in LiteLLMProvider(api_key="k", timeout=0.05).stream(
            "m", [LLMMessage("user", "hi")]
        ):
            pieces.append(piece)

    assert pieces == ["first "], "the gap is bounded, not the stream — chunk 1 must arrive"


def test_a_timeout_is_not_retried_because_it_is_the_upper_bound() -> None:
    """LLM_TIMEOUT_SECONDS is a ceiling. Retrying it 3x makes the ceiling 4x higher,
    which is the wedge the bound exists to stop. A timed-out eval item is re-run by
    `run_eval.py --resume`, which re-pays one item rather than one call."""
    for message in (
        "litellm.Timeout: Connection timed out after 600.0s",
        "litellm.APITimeoutError: Request timed out.",
    ):
        assert LiteLLMProvider._retry_delay(Exception(message), attempt=0) is None


# --- OpenAI-compatible chat gateway ------------------------------------------


def _selected(monkeypatch, **env):
    """_default_provider() under a clean, explicit environment."""
    from app.core.config import get_settings
    from app.services.llm import _default_provider

    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "LLM_API_KEY",
        "LLM_API_BASE",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.setenv(name, env.get(name, ""))
    get_settings.cache_clear()
    try:
        return _default_provider()
    finally:
        get_settings.cache_clear()


def test_an_llm_api_base_selects_the_gateway_with_key_and_base(monkeypatch) -> None:
    provider = _selected(
        monkeypatch, LLM_API_BASE="https://gateway.example/v1", LLM_API_KEY="sk-test"
    )

    assert isinstance(provider, LiteLLMProvider)
    payload = provider._kwargs("openai/some-alias", [LLMMessage("user", "hi")])
    assert payload["api_base"] == "https://gateway.example/v1"
    assert payload["api_key"] == "sk-test"


def test_an_llm_api_base_outranks_a_hosted_provider_key(monkeypatch) -> None:
    """The rule that makes the hybrid decidable: chat on the configured gateway,
    embeddings independently on Gemini. Both are hosted keys, so without an explicit
    precedence the tie-break would pick one and the pairing would work only by luck."""
    provider = _selected(
        monkeypatch,
        GEMINI_API_KEY="gemini-key",
        LLM_API_BASE="https://gateway.example/v1",
        LLM_API_KEY="sk-gateway",
    )

    payload = provider._kwargs("openai/some-alias", [])
    assert payload["api_base"] == "https://gateway.example/v1"
    assert payload["api_key"] == "sk-gateway", "the Gemini key must not reach the gateway"


def test_a_gateway_key_without_a_base_falls_through(monkeypatch) -> None:
    """A key with no endpoint would otherwise be handed to whichever provider the
    model prefix names — a gateway key sent to api.openai.com."""
    assert isinstance(_selected(monkeypatch, LLM_API_KEY="sk-orphan"), OfflineGroundedProvider)


# --- model naming for cost resolution -----------------------------------------


def test_price_model_name_strips_the_litellm_provider_prefix() -> None:
    """Langfuse prices a generation by regex-matching the span's model field against
    definitions whose built-in patterns are bare names, so a routing prefix matches
    nothing and the trace renders no cost at all."""
    from app.services.llm import price_model_name

    assert price_model_name("gemini/gemini-3.5-flash") == "gemini-3.5-flash"
    assert price_model_name("ollama_chat/llama3.2:3b") == "llama3.2:3b"
    assert price_model_name("anthropic/claude-haiku-4-5") == "claude-haiku-4-5"
    # Already bare — the common hosted form — passes through untouched.
    assert price_model_name("gpt-4o") == "gpt-4o"
    # Split once only, so a name that itself contains a slash keeps the remainder.
    assert price_model_name("openrouter/vendor/model-x") == "vendor/model-x"
