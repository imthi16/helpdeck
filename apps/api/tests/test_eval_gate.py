"""The CI eval gate (task 6.4).

This is what blocks a PR, so the interesting cases are the ways a gate can be
wrong in the permissive direction: passing on a missing metric, passing on a run
that mostly errored, or passing a pipeline that refuses everything.
"""

from app.services.evaluation.gate import (
    DEFAULT_THRESHOLDS,
    Threshold,
    evaluate_gate,
    format_gate,
)
from app.services.evaluation.runner import stub_providers

# A pipeline comfortably over every plan threshold.
HEALTHY = {
    "faithfulness": 0.94,
    "context_recall": 0.88,
    "refusal_accuracy": 1.0,
    "false_refusal_rate": 0.08,
}


def test_a_healthy_run_passes_every_threshold() -> None:
    result = evaluate_gate(HEALTHY)

    assert result.passed
    assert result.failures == ()
    assert all(check.passed for check in result.checks)


def test_thresholds_match_the_plan() -> None:
    """The plan names three numbers; drifting from them silently would be a lie."""
    bounds = {t.metric: (t.bound, t.value) for t in DEFAULT_THRESHOLDS}

    assert bounds["faithfulness"] == ("min", 0.85)
    assert bounds["context_recall"] == ("min", 0.70)
    assert bounds["refusal_accuracy"] == ("min", 0.90)


def test_unfaithful_answers_fail_the_gate() -> None:
    result = evaluate_gate(HEALTHY | {"faithfulness": 0.84})

    assert not result.passed
    assert [c.threshold.metric for c in result.failures] == ["faithfulness"]


def test_a_metric_exactly_on_the_bound_passes() -> None:
    """0.85 is the floor, not the first failing value."""
    assert evaluate_gate(HEALTHY | {"faithfulness": 0.85}).passed
    assert evaluate_gate(HEALTHY | {"context_recall": 0.70}).passed


def test_poor_retrieval_and_poor_refusal_both_fail() -> None:
    result = evaluate_gate(HEALTHY | {"context_recall": 0.5, "refusal_accuracy": 0.4})

    assert not result.passed
    assert {c.threshold.metric for c in result.failures} == {
        "context_recall",
        "refusal_accuracy",
    }


def test_a_missing_metric_fails_rather_than_passes() -> None:
    """An empty report must never read as "nothing exceeded the threshold"."""
    result = evaluate_gate({})

    assert not result.passed
    assert all(check.score is None for check in result.checks)
    assert {c.threshold.metric for c in result.failures} == {
        "faithfulness",
        "context_recall",
        "refusal_accuracy",
    }


def test_false_refusal_rate_is_advisory_not_blocking() -> None:
    """6.3 measured 0.40 on this pipeline; gating on it would fail every PR."""
    result = evaluate_gate(HEALTHY | {"false_refusal_rate": 0.40})

    assert result.passed, "advisory thresholds report but do not block"
    assert result.failures == ()
    assert "advisory: false_refusal_rate=0.400" in format_gate(result)


def test_a_pipeline_that_refuses_everything_is_visibly_bad() -> None:
    """Refusing everything aces refusal_accuracy — the advisory is what exposes it."""
    refuse_all = {
        "faithfulness": 1.0,
        "context_recall": 0.88,
        "refusal_accuracy": 1.0,
        "false_refusal_rate": 1.0,
    }
    rendered = format_gate(evaluate_gate(refuse_all))

    assert "false_refusal_rate" in rendered
    assert "ADVISORY" in rendered


def test_advisory_thresholds_can_be_promoted_to_blocking() -> None:
    strict = (
        *(t for t in DEFAULT_THRESHOLDS if t.metric != "false_refusal_rate"),
        Threshold("false_refusal_rate", "max", 0.25, "promoted", advisory=False),
    )
    result = evaluate_gate(HEALTHY | {"false_refusal_rate": 0.40}, strict)

    assert not result.passed
    assert [c.threshold.metric for c in result.failures] == ["false_refusal_rate"]


def test_the_gate_table_lists_every_threshold_checked() -> None:
    """Printing only failures leaves you guessing what CI actually verified."""
    rendered = format_gate(evaluate_gate(HEALTHY))

    for threshold in DEFAULT_THRESHOLDS:
        assert threshold.metric in rendered
    assert "gate PASSED" in rendered


def test_a_failing_gate_explains_each_failure() -> None:
    rendered = format_gate(evaluate_gate(HEALTHY | {"faithfulness": 0.1}))

    assert "gate FAILED" in rendered
    assert "faithfulness=0.100" in rendered
    assert "grounded in retrieved context" in rendered


def test_missing_metrics_render_as_not_measured_not_zero() -> None:
    """Reporting an unmeasured metric as 0.000 states a finding the run never made."""
    rendered = format_gate(evaluate_gate({"faithfulness": 0.9}))

    assert "not measured" in rendered
    assert "0.000" not in rendered
    assert "gate FAILED" in rendered


def test_an_explicitly_unmeasured_metric_is_not_read_as_zero() -> None:
    """A subset with no unanswerable items has undefined refusal accuracy.

    aggregate() emits None rather than mean([]) == 0.0, so the gate must not report
    "refused nothing" for a run that asked no out-of-KB questions.
    """
    result = evaluate_gate(HEALTHY | {"refusal_accuracy": None})
    rendered = format_gate(result)

    assert not result.passed, "an uncertified metric cannot pass the gate"
    assert "refusal_accuracy=not measured" in rendered


# --- stub detection ----------------------------------------------------------
#
# Both stubs are lexical and flatter the pipeline, so a gated run must refuse to
# pass on them. This is the failure that would hurt most: green CI over a pipeline
# that was never actually exercised.

REAL_ENV = {
    "llm_provider": "LiteLLMProvider",
    "embedding_provider": "OllamaEmbeddingProvider",
    "cheap_model": "anthropic/claude-haiku-4-5",
}


def test_a_real_run_reports_no_stubs() -> None:
    assert stub_providers(REAL_ENV) == []


def test_the_offline_answer_stub_is_detected() -> None:
    assert stub_providers(REAL_ENV | {"llm_provider": "OfflineGroundedProvider"}) == [
        "llm_provider=OfflineGroundedProvider"
    ]


def test_the_hashing_embedder_is_detected() -> None:
    """An ANTHROPIC_API_KEY alone hits exactly this: hosted LLM, stub embeddings."""
    env = REAL_ENV | {"embedding_provider": "HashingEmbeddingProvider"}

    assert stub_providers(env) == ["embedding_provider=HashingEmbeddingProvider"]


def test_both_stubs_are_reported_together() -> None:
    env = {
        "llm_provider": "OfflineGroundedProvider",
        "embedding_provider": "HashingEmbeddingProvider",
    }

    assert stub_providers(env) == [
        "embedding_provider=HashingEmbeddingProvider",
        "llm_provider=OfflineGroundedProvider",
    ]


def test_non_string_environment_values_do_not_crash_detection() -> None:
    """environment carries ints and floats too (embedding_dims, thresholds)."""
    assert stub_providers(REAL_ENV | {"embedding_dims": 768, "reranker": None}) == []


def test_a_gateway_url_is_not_mistaken_for_a_stub() -> None:
    """environment gained llm_api_base, a free-text string. The stub scan matches
    exact provider names, so a URL must pass through it untouched."""
    assert stub_providers(REAL_ENV | {"llm_api_base": "https://router.example/v1"}) == []
