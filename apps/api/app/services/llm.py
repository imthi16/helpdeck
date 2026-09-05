"""LLM gateway: the single entry point for chat completions.

Every LLM chat call in the app goes through ``LLMGateway`` — it is the only
module allowed to import a provider chat SDK. Callers pick a ``route``
("cheap" | "strong") which maps to ``LLM_CHEAP_MODEL`` / ``LLM_STRONG_MODEL``.
Token usage and latency are captured on every call and each call is
Langfuse-traced (a no-op when keys are unset).

Without an API key the gateway falls back to a deterministic offline provider so
the app stays runnable and testable. The offline provider is clearly labeled and
is not a substitute for a real model.
"""

import asyncio
import re
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.ingestion.chunker import count_tokens
from app.services.tracing import observation

logger = get_logger(__name__)


@lru_cache(maxsize=8)
def ollama_reachable(base_url: str) -> bool:
    """Whether an Ollama server answers at ``base_url`` (cached per process).

    Lets the app prefer local OSS models when Ollama is up and quietly fall back
    to the offline stubs when it is not (e.g. during tests) — no config toggle
    needed. Cached, so it costs one short socket probe per process.
    """
    if not base_url:
        return False
    parsed = urlparse(base_url)
    host, port = parsed.hostname or "localhost", parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


class LLMRoute(StrEnum):
    cheap = "cheap"
    strong = "strong"


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    text: str
    model: str
    route: LLMRoute
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0


class LLMError(Exception):
    pass


class LLMProvider(Protocol):
    async def complete(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> tuple[str, LLMUsage]: ...

    def stream(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]: ...


class LiteLLMProvider:
    """Real provider-agnostic backend (Ollama, Anthropic, OpenAI, ...) via litellm.

    ``api_base`` targets a self-hosted endpoint (e.g. Ollama at
    ``http://localhost:11434``); ``api_key`` is used for hosted providers. Ollama
    needs neither a key nor any extra SDK.
    """

    #: Retries for transient upstream failures. Hosted free tiers meter aggressively
    #: (Gemini's free tier caps some models at 20 requests/day) and shared capacity
    #: returns 503 under load; a whole eval run should not be voided by either when
    #: a short wait clears it.
    MAX_RATE_LIMIT_RETRIES = 3
    BASE_RETRY_DELAY_SECONDS = 2.0
    #: Providers report how long to wait; prefer their number over our backoff, but
    #: cap it so a "retry in 20 hours" (an exhausted daily quota) fails fast instead
    #: of hanging the run.
    MAX_HONOURED_RETRY_DELAY_SECONDS = 60.0

    _RETRY_DELAY_RE = re.compile(r"retry in (\d+(?:\.\d+)?)s", re.I)
    #: Substrings that mark a failure as worth another attempt: rate limits (429)
    #: and upstream capacity (503). Everything else — a 400, an unknown model, a bad
    #: key — fails identically next time.
    _TRANSIENT_MARKERS = (
        "429",
        "RESOURCE_EXHAUSTED",
        "RateLimit",
        "503",
        "UNAVAILABLE",
        "ServiceUnavailable",
        "overloaded",
        # Gemini's 503 body says only this, with no code in the message text.
        "high demand",
    )

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_base: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout if timeout is not None else get_settings().llm_timeout_seconds

    @classmethod
    def _retry_delay(cls, error: Exception, attempt: int) -> float | None:
        """Seconds to wait before retrying, or None if this is not worth retrying.

        Only rate limits and upstream capacity errors are retried. A 400, a missing
        model, or a bad key will fail identically on the next attempt, so retrying
        them just multiplies the wait before the real error surfaces.
        """
        text = " ".join(str(error).split())
        if not any(marker in text for marker in cls._TRANSIENT_MARKERS):
            return None
        if match := cls._RETRY_DELAY_RE.search(text):
            advised = float(match.group(1))
            if advised > cls.MAX_HONOURED_RETRY_DELAY_SECONDS:
                return None
            return advised
        return cls.BASE_RETRY_DELAY_SECONDS * (2**attempt)

    def _kwargs(self, model: str, messages: list[LLMMessage], **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            # Before **kwargs, so an explicit timeout= from a call site still wins.
            "timeout": self._timeout,
            **kwargs,
        }
        if self._api_key:
            payload["api_key"] = self._api_key
        if self._api_base:
            payload["api_base"] = self._api_base
        return payload

    async def complete(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> tuple[str, LLMUsage]:
        import litellm

        response = await self._with_rate_limit_retry(
            model, lambda: litellm.acompletion(**self._kwargs(model, messages, **kwargs))
        )
        text = response.choices[0].message.content or ""
        usage = LLMUsage(
            prompt_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )
        return text, usage

    async def _with_rate_limit_retry(self, model: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Retry ``call`` through rate limits. Only the initial call is retried —
        a stream that fails mid-iteration has already emitted tokens, so replaying
        it would duplicate them."""
        for attempt in range(self.MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return await call()
            except Exception as exc:  # noqa: BLE001 - classified by _retry_delay
                delay = (
                    None
                    if attempt == self.MAX_RATE_LIMIT_RETRIES
                    else self._retry_delay(exc, attempt)
                )
                if delay is None:
                    raise
                logger.warning(
                    "llm_rate_limited", model=model, attempt=attempt + 1, delay_seconds=delay
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def stream(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        import litellm

        response = await self._with_rate_limit_retry(
            model,
            lambda: litellm.acompletion(**self._kwargs(model, messages, **kwargs), stream=True),
        )
        # The request timeout bounds the HTTP call; it does not bound the gap between
        # two chunks of a stream the server has already accepted. A model evicted
        # mid-generation leaves this loop awaiting a chunk that never arrives — the
        # 7h16m wedge in docs/NOTES.md 6.4 — so every step gets its own deadline.
        chunks = response.__aiter__()
        while True:
            try:
                async with asyncio.timeout(self._timeout):
                    chunk = await chunks.__anext__()
            except StopAsyncIteration:
                # Caught outside the timeout scope: leaking StopAsyncIteration out of
                # an async generator would surface as a bare RuntimeError (PEP 479).
                return
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece


_SENTENCE = re.compile(r"[^.!?]+[.!?]?")
_WORD = re.compile(r"[a-z0-9]+")
_PASSAGE = re.compile(r"\[(\d+)\]\s*(.+)")
_OFFLINE_STOPWORDS = frozenset(
    """a an and are as at be by can do does for from has have how i in is it my no not of
    on or our so that the their to up us was we what when where which who will with you
    your me your please""".split()
)
OFFLINE_REFUSAL = "I don't have enough information to answer that."
_OFFLINE_OVERLAP_THRESHOLD = 0.15


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _OFFLINE_STOPWORDS and len(w) > 1}


class OfflineGroundedProvider:
    """Deterministic offline stand-in used when no API key is configured.

    Not a language model. For grounded prompts it picks the numbered context
    passage with the highest word overlap with the question and answers with its
    first sentence and a ``[n]`` citation — or refuses when nothing overlaps, so
    out-of-KB questions produce a genuine refusal. For routing it returns a
    keyword-matched label; for judging it returns a support score.

    It also answers the two evaluator prompts (per-claim verdicts, reverse
    question generation) so an eval run produces scores with no model server. The
    verdicts are the same lexical-overlap heuristic used everywhere else here: a
    claim with no support in the context genuinely scores unsupported.
    """

    ROUTER_MARKER = "Classify the user request"
    JUDGE_MARKER = "faithfulness"
    # Markers for app/services/evaluation/prompts.py — keep both in sync.
    VERDICT_MARKER = "whether each claim is supported"
    QUESTION_GEN_MARKER = "generate questions"

    async def complete(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> tuple[str, LLMUsage]:
        text = self._respond(messages)
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        return text, LLMUsage(prompt_tokens=prompt_tokens, completion_tokens=len(text.split()))

    async def stream(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        for token in self._respond(messages).split(" "):
            yield token + " "

    def _respond(self, messages: list[LLMMessage]) -> str:
        system = next((m.content for m in messages if m.role == "system"), "")
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        lowered_system = system.lower()

        if self.VERDICT_MARKER in lowered_system:
            return self._verdicts(user)
        if self.QUESTION_GEN_MARKER in lowered_system:
            return self._questions(user)
        if self.JUDGE_MARKER in lowered_system:
            return self._judge(user)
        if self.ROUTER_MARKER.lower() in lowered_system:
            return self._route(user)
        return self._answer(user)

    def _route(self, user: str) -> str:
        lowered = user.lower()
        if any(w in lowered for w in ("human", "agent", "representative", "person")):
            return "human_request"
        if any(w in lowered for w in ("hi ", "hello", "thanks", "thank you", "bye")):
            return "chitchat"
        return "faq"

    def _judge(self, user: str) -> str:
        # Supported when the answer's cited passages overlap the answer content.
        answer = user.split("Answer:", 1)[-1]
        return "1.0" if re.search(r"\[\d+\]", answer) else "0.0"

    def _verdicts(self, user: str) -> str:
        """One ``n: yes|no`` line per claim, by lexical support in the context.

        A claim counts as supported when most of its content words appear in the
        passages the answer was allowed to see — the offline analogue of an
        entailment check, and enough to catch a claim invented out of thin air.
        """
        context, _, claims_block = user.partition("Claims:")
        context_words = _content_words(context)
        lines: list[str] = []
        for line in claims_block.strip().splitlines():
            number, _, claim = line.partition(":")
            claim_words = _content_words(claim or line)
            if not claim_words:
                continue
            overlap = len(claim_words & context_words) / len(claim_words)
            lines.append(f"{number.strip()}: {'yes' if overlap >= 0.6 else 'no'}")
        return "\n".join(lines)

    def _questions(self, user: str) -> str:
        """Echo the answer back as the question it responds to.

        Degenerate on purpose: without a model there is nothing to paraphrase
        with, so offline answer-relevancy reduces to question/answer lexical
        similarity. Labelled as such in the eval report.
        """
        return user.split("Answer:", 1)[-1].strip()

    def _answer(self, user: str) -> str:
        question = user.split("Question:", 1)[-1] if "Question:" in user else user
        q_words = _content_words(question)
        if not q_words:
            return OFFLINE_REFUSAL

        best_n: str | None = None
        best_body = ""
        best_overlap = 0.0
        for match in _PASSAGE.finditer(user):
            body = match.group(2)
            overlap = len(q_words & _content_words(body)) / len(q_words)
            if overlap > best_overlap:
                best_overlap, best_n, best_body = overlap, match.group(1), body

        if best_n is None or best_overlap < _OFFLINE_OVERLAP_THRESHOLD:
            return OFFLINE_REFUSAL
        return f"{_first_sentence(best_body)} [{best_n}]"


def _first_sentence(text: str) -> str:
    text = text.strip()
    found = _SENTENCE.search(text)
    return found.group(0).strip() if found else text


def _default_provider() -> LLMProvider:
    settings = get_settings()
    # Order is only a tie-break for the unusual case of several keys being set;
    # which models actually run is decided by LLM_CHEAP_MODEL / LLM_STRONG_MODEL,
    # whose litellm prefix has to match the key you supplied.
    hosted_key = (
        settings.anthropic_api_key or settings.openai_api_key or settings.gemini_api_key or None
    )
    if hosted_key:
        return LiteLLMProvider(hosted_key)
    if ollama_reachable(settings.ollama_base_url):
        logger.info("llm_ollama_mode", base_url=settings.ollama_base_url)
        return LiteLLMProvider(api_base=settings.ollama_base_url)
    logger.warning(
        "llm_offline_mode", reason="no LLM API key and Ollama unreachable; using offline provider"
    )
    return OfflineGroundedProvider()


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        cheap_model: str | None = None,
        strong_model: str | None = None,
    ) -> None:
        settings = get_settings()
        self._provider = provider or _default_provider()
        self._cheap_model = cheap_model or settings.llm_cheap_model or "ollama_chat/llama3.2:3b"
        self._strong_model = strong_model or settings.llm_strong_model or "ollama_chat/qwen2.5:7b"

    @property
    def provider_name(self) -> str:
        """Which backend is answering — the eval report labels runs with this, so
        offline-stub scores are never mistaken for real model scores."""
        return type(self._provider).__name__

    def model_for(self, route: LLMRoute) -> str:
        return self._strong_model if route == LLMRoute.strong else self._cheap_model

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        route: LLMRoute = LLMRoute.cheap,
        **kwargs: Any,
    ) -> LLMResponse:
        model = self.model_for(route)
        start = time.perf_counter()
        try:
            with observation(
                "llm.complete",
                as_type="generation",
                model=model,
                input=[m.__dict__ for m in messages],
            ) as generation:
                text, usage = await self._provider.complete(model, messages, **kwargs)
                generation.update(
                    output=text,
                    usage_details={
                        "input": usage.prompt_tokens,
                        "output": usage.completion_tokens,
                    },
                )
        except Exception as exc:
            raise LLMError(str(exc)) from exc
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "llm_complete",
            model=model,
            route=str(route),
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            latency_ms=latency_ms,
        )
        return LLMResponse(text=text, model=model, route=route, usage=usage, latency_ms=latency_ms)

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        route: LLMRoute = LLMRoute.cheap,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        model = self.model_for(route)
        pieces: list[str] = []
        start = time.perf_counter()
        # A streamed response carries no usage payload, so tokens are counted
        # locally with tiktoken and the span says so rather than implying the
        # provider reported them.
        with observation(
            "llm.stream", as_type="generation", model=model, input=[m.__dict__ for m in messages]
        ) as generation:
            async for piece in self._provider.stream(model, messages, **kwargs):
                pieces.append(piece)
                yield piece
            text = "".join(pieces)
            tokens_in = count_tokens("\n".join(m.content for m in messages))
            tokens_out = count_tokens(text)
            generation.update(
                output=text,
                usage_details={"input": tokens_in, "output": tokens_out},
                metadata={"usage_source": "estimated (tiktoken); streamed response has no usage"},
            )
        logger.info(
            "llm_stream",
            model=model,
            route=str(route),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.perf_counter() - start) * 1000),
        )
