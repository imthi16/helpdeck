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

import re
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.tracing import get_langfuse

logger = get_logger(__name__)


PROBE_FAILURE_TTL_SECONDS = 30.0
# base_url -> (reachable, monotonic time after which a failed probe is retried)
_probe_cache: dict[str, tuple[bool, float]] = {}


def ollama_reachable(base_url: str) -> bool:
    """Whether an Ollama server answers at ``base_url``.

    Lets the app prefer local OSS models when Ollama is up and quietly fall back
    to the offline stubs when it is not (e.g. during tests) — no config toggle
    needed. A successful probe is cached for the life of the process; a failed
    probe is retried after a short TTL, so a process that starts before Ollama
    is healthy switches to it once it comes online.
    """
    if not base_url:
        return False
    cached = _probe_cache.get(base_url)
    if cached is not None:
        reachable, retry_at = cached
        if reachable or time.monotonic() < retry_at:
            return reachable
    parsed = urlparse(base_url)
    host, port = parsed.hostname or "localhost", parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=0.3):
            reachable = True
    except OSError:
        reachable = False
    _probe_cache[base_url] = (reachable, time.monotonic() + PROBE_FAILURE_TTL_SECONDS)
    return reachable


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

    def __init__(self, api_key: str | None = None, *, api_base: str | None = None) -> None:
        self._api_key = api_key
        self._api_base = api_base

    def _kwargs(self, model: str, messages: list[LLMMessage], **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
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

        response = await litellm.acompletion(**self._kwargs(model, messages, **kwargs))
        text = response.choices[0].message.content or ""
        usage = LLMUsage(
            prompt_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )
        return text, usage

    async def stream(
        self, model: str, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        import litellm

        response = await litellm.acompletion(**self._kwargs(model, messages, **kwargs), stream=True)
        async for chunk in response:
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
    """

    ROUTER_MARKER = "Classify the user request"
    JUDGE_MARKER = "faithfulness"

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

        if self.JUDGE_MARKER in system.lower():
            return self._judge(user)
        if self.ROUTER_MARKER.lower() in system.lower():
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
    if settings.anthropic_api_key or settings.openai_api_key:
        return LiteLLMProvider(settings.anthropic_api_key or settings.openai_api_key or None)
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
        langfuse = get_langfuse()
        start = time.perf_counter()
        try:
            if langfuse is None:
                text, usage = await self._provider.complete(model, messages, **kwargs)
            else:
                with langfuse.start_as_current_observation(
                    name="llm.complete",
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
        langfuse = get_langfuse()
        # Explicit generation object, never a context manager: this is an
        # async generator, and holding a contextvar-based span across yields
        # corrupts the OTEL context. It still parents under the caller's
        # current span (the answer node) because that context is captured at
        # creation time.
        generation = (
            langfuse.start_observation(
                name="llm.stream",
                as_type="generation",
                model=model,
                input=[m.__dict__ for m in messages],
            )
            if langfuse is not None
            else None
        )
        pieces: list[str] = []
        try:
            async for piece in self._provider.stream(model, messages, **kwargs):
                pieces.append(piece)
                yield piece
        finally:
            if generation is not None:
                generation.update(output="".join(pieces))
                generation.end()
