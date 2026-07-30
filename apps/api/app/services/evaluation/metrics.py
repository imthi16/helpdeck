"""RAGAS-style metrics for the grounded RAG pipeline.

These are our own implementations of RAGAS's published metric definitions, not
the ``ragas`` package: every LLM and embedding call has to go through
``app/services/llm.py`` / ``app/services/embeddings.py`` (so it is gateway-routed
and Langfuse-traced), and the retrieval metrics are better served by the golden
set's ground-truth document labels than by an LLM judge. Report wording must say
"RAGAS-style", never "scored by RAGAS".

Where we depart from RAGAS, and why:

- **faithfulness** — RAGAS decomposes the answer into "simple statements" with an
  LLM, then entails each against the context. We split statements
  *deterministically* (sentences) so the denominator is reproducible across runs;
  only the entailment verdicts cost an LLM call. Score = supported / total claims.
- **answer_relevancy** — as RAGAS: generate questions back from the answer, mean
  cosine similarity against the real question. Refusals are excluded rather than
  scored 0 (a correct refusal is not an irrelevant answer; refusal quality is
  measured by ``refusal_accuracy``).
- **context_precision / context_recall** — RAGAS judges chunk relevance with an
  LLM against the ground-truth answer. We use ``expected_doc_ids`` from the golden
  set instead: relevance is a fact about the corpus, so a label beats an opinion.
  The trade is granularity — relevance is per *document*, not per chunk — and
  precision is therefore pessimistic for an answer whose document supplies
  several chunks. Precision stays rank-weighted (mean of precision@k over the
  positions holding a relevant chunk), like RAGAS, so ranking the right document
  first scores higher than ranking it last.

**The judge runs on the strong route** (``JUDGE_ROUTE``), unlike the pipeline's own
in-line faithfulness gate, which is cheap because a user is waiting. Nobody waits
on an eval, and a judge too weak for the task does not add noise — it adds bias,
in the direction that reads as hallucination. Measured on the 30-item fast subset:
``llama3.2:3b`` scored a verbatim-supported claim ("the return window is 30 days
from the date the carrier marks the package as delivered", stated in ``returns.md``)
as unsupported, dragging faithfulness to 0.611 on a pipeline whose answers were
largely correct. ``qwen2.5:7b`` rules the same claim supported. Pass ``route=`` to
override — a hosted judge is better still.
"""

import math
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any

from app.agent import prompts as agent_prompts
from app.services.embeddings import EmbeddingService
from app.services.evaluation import prompts
from app.services.llm import LLMGateway, LLMMessage, LLMRoute

_CITATION_RE = re.compile(r"\[\d+\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_VERDICT_RE = re.compile(
    r"^\s*(\d+)\s*[:.\)-]?\s*(yes|no|true|false|supported|unsupported)\b", re.I
)
_POSITIVE_VERDICTS = {"yes", "true", "supported"}
_MIN_CLAIM_CHARS = 12
_MAX_GENERATED_QUESTIONS = 3

#: Which model judges. Strong by default — see the module docstring: an eval is
#: offline, so judgment quality is worth far more than the latency it costs.
JUDGE_ROUTE = LLMRoute.strong


@dataclass
class FaithfulnessScore:
    """Supported-claim ratio plus the per-claim detail behind it."""

    score: float
    claims: tuple[str, ...]
    supported: tuple[bool, ...]

    @property
    def supported_count(self) -> int:
        return sum(self.supported)


def split_claims(answer: str) -> list[str]:
    """Split an answer into the claims faithfulness is scored over.

    Sentence-level and deterministic (see module docstring). Citation markers are
    stripped so a claim is judged on its content, and fragments too short to
    assert anything are dropped.
    """
    cleaned = _CITATION_RE.sub(" ", answer)
    claims: list[str] = []
    for piece in _SENTENCE_SPLIT_RE.split(cleaned):
        claim = " ".join(piece.split())
        if len(claim) >= _MIN_CLAIM_CHARS:
            claims.append(claim)
    return claims


def parse_verdicts(text: str, expected: int) -> list[bool]:
    """Parse ``n: yes`` lines into ``expected`` booleans.

    A claim the judge did not rule on counts as unsupported: silence must never
    inflate faithfulness.
    """
    verdicts: dict[int, bool] = {}
    for line in text.splitlines():
        match = _VERDICT_RE.match(line)
        if match:
            # First verdict wins. The prompt asks for one line per claim in order, so a
            # repeated number is a weak model rambling, not correcting itself — and
            # last-write-wins let a stray trailing line silently flip an already-ruled
            # claim (llama3.2:3b emits "1: yes\n1: no" and the "no" used to stick).
            verdicts.setdefault(int(match.group(1)), match.group(2).lower() in _POSITIVE_VERDICTS)
    return [verdicts.get(i, False) for i in range(1, expected + 1)]


async def faithfulness(
    gateway: LLMGateway,
    answer: str,
    chunks: Sequence[dict[str, Any]],
    *,
    route: LLMRoute = JUDGE_ROUTE,
) -> FaithfulnessScore:
    """Fraction of the answer's claims the retrieved context supports."""
    claims = split_claims(answer)
    if not claims or not chunks:
        # No claims means nothing was asserted, so nothing can be unfaithful.
        return FaithfulnessScore(1.0 if not claims else 0.0, tuple(claims), ())
    response = await gateway.complete(
        [
            LLMMessage("system", prompts.CLAIM_VERDICT_SYSTEM),
            LLMMessage("user", prompts.build_claim_verdict_prompt(claims, chunks)),
        ],
        route=route,
    )
    supported = parse_verdicts(response.text, len(claims))
    return FaithfulnessScore(
        score=sum(supported) / len(claims),
        claims=tuple(claims),
        supported=tuple(supported),
    )


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def answer_relevancy(
    gateway: LLMGateway,
    embeddings: EmbeddingService,
    question: str,
    answer: str,
    *,
    route: LLMRoute = JUDGE_ROUTE,
) -> float:
    """Mean cosine similarity between the question and questions regenerated from the answer.

    Measures whether the answer addresses what was asked (RAGAS answer relevancy).
    Clamped to [0, 1]: cosine can go negative, but "less relevant than nothing"
    is not a distinction worth averaging.
    """
    if not answer.strip():
        return 0.0
    response = await gateway.complete(
        [
            LLMMessage("system", prompts.QUESTION_GEN_SYSTEM),
            LLMMessage("user", prompts.build_question_gen_prompt(answer)),
        ],
        route=route,
    )
    generated = [line.strip() for line in response.text.splitlines() if line.strip()]
    generated = generated[:_MAX_GENERATED_QUESTIONS]
    if not generated:
        return 0.0
    vectors = await embeddings.embed_texts([question, *generated])
    scores = [cosine_similarity(vectors[0], vector) for vector in vectors[1:]]
    return max(0.0, min(1.0, sum(scores) / len(scores)))


def context_precision(retrieved_doc_ids: Sequence[str], expected_doc_ids: Collection[str]) -> float:
    """Rank-weighted precision: mean precision@k over positions holding a relevant chunk.

    Undefined without a ground-truth label, so unanswerable items return 0.0 and
    the runner excludes them from the aggregate rather than averaging them in.
    """
    expected = set(expected_doc_ids)
    if not expected or not retrieved_doc_ids:
        return 0.0
    hits = 0
    precisions: list[float] = []
    for rank, doc_id in enumerate(retrieved_doc_ids, start=1):
        if doc_id in expected:
            hits += 1
            precisions.append(hits / rank)
    return sum(precisions) / len(precisions) if precisions else 0.0


def context_recall(retrieved_doc_ids: Sequence[str], expected_doc_ids: Collection[str]) -> float:
    """Share of the expected documents that retrieval actually surfaced."""
    expected = set(expected_doc_ids)
    if not expected:
        return 0.0
    return len(expected & set(retrieved_doc_ids)) / len(expected)


def is_refusal(answer: str, *, escalated: bool) -> bool:
    """Whether a turn declined to answer.

    Escalation counts: the handoff message is how the pipeline refuses out loud.
    The literal refusal text is matched too, for the pre-judge path where the
    answer node refuses before any gate runs.
    """
    if escalated:
        return True
    normalized = " ".join(answer.split()).lower()
    return agent_prompts.REFUSAL_TEXT.lower() in normalized


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
