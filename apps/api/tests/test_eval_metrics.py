"""RAGAS-style metric implementations (task 6.3).

These are the numbers the CI gate (6.4) and the in-product Quality card (6.6)
will be read off, so each one is pinned to a hand-computed expectation rather
than "looks about right".
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.services.evaluation import prompts
from app.services.evaluation.dataset import (
    GoldenDatasetError,
    load_golden,
    select_subset,
)
from app.services.evaluation.metrics import answer_relevancy as answer_relevancy_metric
from app.services.evaluation.metrics import (
    context_precision,
    context_recall,
    cosine_similarity,
    faithfulness,
    is_refusal,
    mean,
    parse_verdicts,
    split_claims,
)
from app.services.llm import (
    LLMGateway,
    LLMMessage,
    LLMRoute,
    LLMUsage,
    OfflineGroundedProvider,
)

CHUNKS = [
    {"content": "Most items can be returned within 30 days of delivery."},
    {"content": "Opened bags of coffee beans cannot be returned."},
]


class ScriptedProvider:
    """Returns canned text so metric arithmetic is tested, not a model."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []
        self.models: list[str] = []

    async def complete(self, model, messages, **kwargs):
        self.prompts.append("\n".join(m.content for m in messages))
        self.models.append(model)
        return self.text, LLMUsage(1, 1)

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        yield self.text


def gateway_for(text: str) -> tuple[LLMGateway, ScriptedProvider]:
    provider = ScriptedProvider(text)
    return LLMGateway(provider, cheap_model="c", strong_model="s"), provider


class StubEmbeddings:
    """Two-dimensional vectors keyed by text, so cosines are hand-checkable."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors[text] for text in texts]


# --- claim splitting ---------------------------------------------------------


def test_split_claims_drops_citations_and_fragments() -> None:
    answer = "Returns take 30 days [1]. Refunds land in 5 days [2]. Ok."
    claims = split_claims(answer)

    assert claims == ["Returns take 30 days .", "Refunds land in 5 days ."]
    assert all("[" not in claim for claim in claims)


def test_split_claims_on_empty_answer() -> None:
    assert split_claims("") == []
    assert split_claims("   ") == []


# --- verdict parsing ---------------------------------------------------------


def test_parse_verdicts_reads_numbered_lines() -> None:
    assert parse_verdicts("1: yes\n2: no\n3: yes", 3) == [True, False, True]
    assert parse_verdicts("1. supported\n2. unsupported", 2) == [True, False]


def test_unruled_claims_count_as_unsupported() -> None:
    """Silence must never inflate faithfulness."""
    assert parse_verdicts("1: yes", 3) == [True, False, False]
    assert parse_verdicts("no idea", 2) == [False, False]


def test_duplicate_verdict_lines_keep_the_first_ruling() -> None:
    """A rambling judge must not overwrite a claim it already ruled on.

    llama3.2:3b really does answer "1: yes\\n1: no" to a single claim; under
    last-write-wins the stray line silently flipped supported claims to
    unsupported, which reads as hallucination in the report.
    """
    assert parse_verdicts("1: yes\n1: no", 1) == [True]
    assert parse_verdicts("1: no\n1: yes", 1) == [False]
    assert parse_verdicts("1: yes\n2: no\n2: yes", 2) == [True, False]


# --- faithfulness ------------------------------------------------------------


async def test_faithfulness_is_supported_over_total_claims() -> None:
    gateway, _ = gateway_for("1: yes\n2: no")

    score = await faithfulness(gateway, "Claim one is here [1]. Claim two is here [2].", CHUNKS)

    assert score.claims == ("Claim one is here .", "Claim two is here .")
    assert score.supported == (True, False)
    assert score.score == 0.5
    assert score.supported_count == 1


async def test_faithfulness_of_a_claimless_answer_is_one() -> None:
    """Nothing asserted means nothing unfaithful — not a zero."""
    gateway, provider = gateway_for("1: yes")

    score = await faithfulness(gateway, "Sure.", CHUNKS)

    assert score.score == 1.0
    assert provider.prompts == [], "no judge call needed for an answer with no claims"


async def test_faithfulness_without_context_is_zero() -> None:
    gateway, provider = gateway_for("1: yes")

    score = await faithfulness(gateway, "The warranty lasts three years.", [])

    assert score.score == 0.0
    assert provider.prompts == []


async def test_faithfulness_prompt_carries_numbered_claims_and_context() -> None:
    gateway, provider = gateway_for("1: yes")

    await faithfulness(gateway, "Returns take 30 days [1].", CHUNKS)

    sent = provider.prompts[0]
    assert "1: Returns take 30 days ." in sent
    assert "[1] Most items can be returned within 30 days of delivery." in sent


# --- which model judges ------------------------------------------------------


async def test_the_judge_runs_on_the_strong_route_by_default() -> None:
    """Nobody waits on an eval, and a weak judge biases rather than just adds noise."""
    gateway, provider = gateway_for("1: yes")

    await faithfulness(gateway, "Returns take 30 days [1].", CHUNKS)

    assert provider.models == ["s"]


async def test_the_judge_route_is_overridable() -> None:
    gateway, provider = gateway_for("1: yes")

    await faithfulness(gateway, "Returns take 30 days [1].", CHUNKS, route=LLMRoute.cheap)

    assert provider.models == ["c"]


async def test_question_generation_also_defaults_to_the_strong_route() -> None:
    gateway, provider = gateway_for("q one")
    embeddings = StubEmbeddings({"q": [1.0, 0.0], "q one": [1.0, 0.0]})

    await answer_relevancy_metric(gateway, embeddings, "q", "an answer")

    assert provider.models == ["s"]


# --- answer relevancy --------------------------------------------------------


async def test_answer_relevancy_averages_cosines_of_generated_questions() -> None:
    gateway, _ = gateway_for("q one\nq two")
    embeddings = StubEmbeddings(
        {
            "the question": [1.0, 0.0],
            "q one": [1.0, 0.0],  # cosine 1.0
            "q two": [0.0, 1.0],  # cosine 0.0
        }
    )

    score = await answer_relevancy_metric(gateway, embeddings, "the question", "an answer")

    assert score == pytest.approx(0.5)


async def test_answer_relevancy_of_an_empty_answer_is_zero() -> None:
    gateway, provider = gateway_for("q")
    score = await answer_relevancy_metric(gateway, StubEmbeddings({}), "q?", "  ")
    assert score == 0.0
    assert provider.prompts == []


async def test_answer_relevancy_never_goes_negative() -> None:
    gateway, _ = gateway_for("opposite")
    embeddings = StubEmbeddings({"q": [1.0, 0.0], "opposite": [-1.0, 0.0]})

    assert await answer_relevancy_metric(gateway, embeddings, "q", "an answer") == 0.0


def test_cosine_similarity() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # no NaN on a zero vector


# --- retrieval metrics -------------------------------------------------------


def test_context_precision_rewards_ranking_the_right_document_first() -> None:
    first = context_precision(["returns", "faq", "shipping"], {"returns"})
    last = context_precision(["faq", "shipping", "returns"], {"returns"})

    assert first == pytest.approx(1.0)
    assert last == pytest.approx(1 / 3)
    assert first > last


def test_context_precision_averages_precision_at_each_hit() -> None:
    # hits at ranks 1 and 3 -> mean(1/1, 2/3)
    assert context_precision(["returns", "faq", "returns"], {"returns"}) == pytest.approx(
        (1.0 + 2 / 3) / 2
    )


def test_context_precision_is_zero_without_a_hit_or_a_label() -> None:
    assert context_precision(["faq"], {"returns"}) == 0.0
    assert context_precision([], {"returns"}) == 0.0
    assert context_precision(["returns"], set()) == 0.0  # unanswerable item


def test_context_recall_counts_distinct_expected_documents() -> None:
    assert context_recall(["warranty", "warranty"], {"warranty", "descaling"}) == 0.5
    assert context_recall(["warranty", "descaling"], {"warranty", "descaling"}) == 1.0
    assert context_recall(["faq"], {"warranty"}) == 0.0
    assert context_recall([], set()) == 0.0


# --- refusal detection -------------------------------------------------------


def test_escalation_counts_as_a_refusal() -> None:
    assert is_refusal("I'm connecting you with a member of our support team.", escalated=True)


def test_literal_refusal_text_is_detected_without_escalation() -> None:
    assert is_refusal("I don't have enough information to answer that.", escalated=False)


def test_a_cited_answer_is_not_a_refusal() -> None:
    assert not is_refusal("Returns take 30 days [1].", escalated=False)


def test_mean_of_nothing_is_zero() -> None:
    assert mean([]) == 0.0
    assert mean([1.0, 0.0]) == 0.5


# --- dataset loader ----------------------------------------------------------


def test_load_golden_reads_the_committed_dataset() -> None:
    items = load_golden()

    assert len(items) >= 100
    assert all(item.question and item.ground_truth for item in items)
    unanswerable = [item for item in items if not item.answerable]
    assert unanswerable and all(item.expected_doc_ids == () for item in unanswerable)


def test_select_subset_filters_by_tag_and_keeps_dataset_order() -> None:
    items = load_golden()
    fast = select_subset(items, tag="fast")

    assert len(fast) == 30
    assert [i.id for i in fast] == [i.id for i in items if "fast" in i.tags]
    assert [i.id for i in select_subset(items, limit=3)] == [i.id for i in items[:3]]


def test_select_subset_rejects_an_unknown_tag() -> None:
    with pytest.raises(GoldenDatasetError):
        select_subset(load_golden(), tag="nope")


def test_loader_rejects_malformed_lines(tmp_path: Path) -> None:
    bad = tmp_path / "golden.jsonl"
    bad.write_text('{"id": "g1", "question": "q"}\n')
    with pytest.raises(GoldenDatasetError, match="missing keys"):
        load_golden(bad)

    bad.write_text("not json\n")
    with pytest.raises(GoldenDatasetError, match="invalid JSON"):
        load_golden(bad)

    with pytest.raises(GoldenDatasetError, match="not found"):
        load_golden(tmp_path / "absent.jsonl")


# --- offline provider support for the evaluator prompts ----------------------


async def test_offline_provider_scores_claims_by_lexical_support() -> None:
    """The offline stub must answer the evaluator prompts, or an eval run with no
    model server would score every claim unsupported and report a false zero."""
    provider = OfflineGroundedProvider()
    user = prompts.build_claim_verdict_prompt(
        ["Most items can be returned within 30 days of delivery.", "The boiler is 1400 watts."],
        CHUNKS,
    )

    text, _ = await provider.complete(
        "m",
        [
            LLMMessage("system", prompts.CLAIM_VERDICT_SYSTEM),
            LLMMessage("user", user),
        ],
    )

    assert parse_verdicts(text, 2) == [True, False]


async def test_offline_provider_generates_a_question_from_the_answer() -> None:
    provider = OfflineGroundedProvider()
    text, _ = await provider.complete(
        "m",
        [
            LLMMessage("system", prompts.QUESTION_GEN_SYSTEM),
            LLMMessage("user", prompts.build_question_gen_prompt("Returns take 30 days.")),
        ],
    )

    assert text == "Returns take 30 days."
