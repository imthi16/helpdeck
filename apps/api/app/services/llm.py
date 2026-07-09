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
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.tracing import get_langfuse

logger = get_logger(__name__)


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
    """Real provider-agnostic backend (Anthropic, OpenAI, ...) via litellm."""

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def _kwargs(self, model: str, messages: list[LLMMessage], **kwargs: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            **kwargs,
        }
        if self._api_key:
            payload["api_key"] = self._api_key
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


class OfflineGroundedProvider:
    """Deterministic offline stand-in used when no API key is configured.

    Not a language model. For grounded prompts it echoes the first sentence of
    the provided numbered context with a ``[n]`` citation so the agent graph,
    guardrails, and streaming can be exercised without a provider. For routing
    (single-word classification) prompts it returns a keyword-matched label.
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
            # Offline judge: supported if the answer shares a citation with context.
            return "1.0" if "[1]" in user or "[2]" in user else "0.0"

        if self.ROUTER_MARKER.lower() in system.lower():
            lowered = user.lower()
            if any(w in lowered for w in ("human", "agent", "representative", "person")):
                return "human_request"
            if any(w in lowered for w in ("hi", "hello", "thanks", "thank you", "bye")):
                return "chitchat"
            return "faq"

        # Grounded answer: cite the first numbered context passage if present.
        match = re.search(r"\[(\d+)\]\s*(.+)", user)
        if match:
            snippet = _first_sentence(match.group(2))
            return f"{snippet} [{match.group(1)}]"
        return "I don't have enough information to answer that."


def _first_sentence(text: str) -> str:
    text = text.strip()
    found = _SENTENCE.search(text)
    return found.group(0).strip() if found else text


def _default_provider() -> LLMProvider:
    settings = get_settings()
    if settings.anthropic_api_key or settings.openai_api_key:
        return LiteLLMProvider(settings.anthropic_api_key or settings.openai_api_key or None)
    logger.warning("llm_offline_mode", reason="no LLM API key set; using offline provider")
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
        self._cheap_model = cheap_model or settings.llm_cheap_model or "claude-haiku-4-5-20251001"
        self._strong_model = strong_model or settings.llm_strong_model or "claude-sonnet-5"

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
                with langfuse.start_as_current_generation(
                    name="llm.complete", model=model, input=[m.__dict__ for m in messages]
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
        async for piece in self._provider.stream(model, messages, **kwargs):
            yield piece
