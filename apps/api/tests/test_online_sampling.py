"""Nightly online quality sampling (task 6.5).

The interesting cases are the ways a quality signal can lie: averaging refusals in,
alerting on a night with nothing to measure, judging an answer against a truncated
snippet, or letting one tenant's scores land on another's row.
"""

import random
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import prompts as agent_prompts
from app.models import (
    Conversation,
    ConversationChannel,
    EvalRun,
    EvalRunKind,
    Message,
    MessageRole,
    Organization,
)
from app.services.evaluation.online import (
    MIN_SAMPLE,
    OnlineRunResult,
    ScoredAnswer,
    build_eval_run,
    is_scorable,
    mean_confidence_gap,
    run_online_sampling,
    sample_size,
    should_alert,
)
from app.services.llm import LLMGateway, LLMUsage

Sessionmaker = async_sessionmaker[AsyncSession]


class ScriptedJudge:
    """Returns a fixed verdict line so metric arithmetic is tested, not a model."""

    def __init__(self, text: str = "1: yes") -> None:
        self.text = text
        self.calls = 0

    async def complete(self, model, messages, **kwargs):
        self.calls += 1
        return self.text, LLMUsage(1, 1)

    async def stream(self, model, messages, **kwargs):
        yield self.text


def gateway_for(text: str = "1: yes") -> tuple[LLMGateway, ScriptedJudge]:
    provider = ScriptedJudge(text)
    return LLMGateway(provider, cheap_model="c", strong_model="s"), provider


# --- sample sizing -----------------------------------------------------------


def test_sample_size_is_a_share_of_the_population() -> None:
    assert sample_size(1000, rate=0.05) == 50


def test_a_quiet_day_still_yields_a_usable_sample() -> None:
    """5% of 20 is 1; a one-item average is noise the trend would treat as signal."""
    assert sample_size(20, rate=0.05) == MIN_SAMPLE


def test_the_sample_never_exceeds_the_population() -> None:
    assert sample_size(3, rate=0.05) == 3


def test_a_busy_day_is_capped() -> None:
    assert sample_size(100_000, rate=0.05) == 100


def test_no_traffic_means_no_sample() -> None:
    assert sample_size(0) == 0


# --- what is scorable --------------------------------------------------------


def _message(content: str, citations: list[dict] | None = None) -> Message:
    return Message(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role=MessageRole.assistant,
        content=content,
        citations=citations if citations is not None else [{"chunk_id": str(uuid.uuid4())}],
    )


def test_a_cited_answer_is_scorable() -> None:
    assert is_scorable(_message("Returns take 30 days [1]."))


def test_a_refusal_is_excluded_rather_than_scored_zero() -> None:
    """A refusal asserts nothing, so it cannot be unfaithful."""
    assert not is_scorable(_message(agent_prompts.REFUSAL_TEXT))


def test_an_uncited_answer_is_excluded() -> None:
    """With no recorded context, a score would measure our missing data."""
    assert not is_scorable(_message("Some answer with no citations.", citations=[]))


# --- alerting ----------------------------------------------------------------


def _result(mean: float | None, baseline: float | None) -> OnlineRunResult:
    return OnlineRunResult(
        org_id=uuid.uuid4(),
        sampled=10,
        scored=10,
        skipped_refusals=0,
        skipped_uncited=0,
        errors=0,
        mean_faithfulness=mean,
        baseline_faithfulness=baseline,
    )


def test_a_real_regression_alerts() -> None:
    assert should_alert(_result(0.80, 0.92))


def test_a_drop_below_the_threshold_does_not_alert() -> None:
    """Deliberately not asserted at the exact boundary: 0.9 - 0.05 rounds so that
    0.9 - 0.85 == 0.050000000000000044, so "exactly ALERT_DROP" is not a state a
    float can hold. Pinning it would test IEEE-754 rather than the alert rule."""
    assert not should_alert(_result(0.90, 0.94))
    assert should_alert(_result(0.90, 0.97))


def test_an_improvement_does_not_alert() -> None:
    assert not should_alert(_result(0.95, 0.80))


def test_the_first_ever_run_does_not_alert() -> None:
    """Nothing to have dropped from — alerting here cries wolf on day one."""
    assert not should_alert(_result(0.10, None))


def test_a_night_with_nothing_scorable_does_not_alert() -> None:
    """No measurement means no regression, however bad the baseline looks."""
    assert not should_alert(_result(None, 0.95))


# --- the recorded row --------------------------------------------------------


def test_the_row_is_org_scoped_and_marked_online() -> None:
    run = _result(0.7, 0.9)
    row = build_eval_run(run)

    assert row.kind == EvalRunKind.online
    assert row.org_id == run.org_id
    assert row.metrics["faithfulness"] == 0.7
    assert row.metrics["drop"] == pytest.approx(0.2)


def test_an_online_run_records_no_gate_verdict() -> None:
    """Sampling reports; it does not block. A `passed` here would read as a gate."""
    assert build_eval_run(_result(0.1, 0.9)).passed is None


def test_the_confidence_gap_compares_judge_against_the_serving_gate() -> None:
    answers = [
        ScoredAnswer(uuid.uuid4(), uuid.uuid4(), None, 0.9, 2, 2, pipeline_confidence=0.5),
        ScoredAnswer(uuid.uuid4(), uuid.uuid4(), None, 0.8, 2, 2, pipeline_confidence=0.4),
    ]

    assert mean_confidence_gap(answers) == pytest.approx(0.4)


def test_the_confidence_gap_ignores_answers_the_gate_never_scored() -> None:
    answers = [ScoredAnswer(uuid.uuid4(), uuid.uuid4(), None, 0.9, 1, 1, None)]

    assert mean_confidence_gap(answers) is None


# --- end to end against the database -----------------------------------------


async def _seed_org_with_answers(
    session: AsyncSession, *, name: str, answers: list[tuple[str, list[dict]]]
) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=name)
    session.add(org)
    await session.flush()
    convo = Conversation(id=uuid.uuid4(), org_id=org.id, channel=ConversationChannel.widget)
    session.add(convo)
    await session.flush()
    for content, citations in answers:
        session.add(
            Message(
                id=uuid.uuid4(),
                org_id=org.id,
                conversation_id=convo.id,
                role=MessageRole.assistant,
                content=content,
                citations=citations,
            )
        )
    await session.flush()
    return org.id


async def test_refusals_are_not_averaged_into_the_score(
    db_sessionmaker: Sessionmaker,
) -> None:
    """A cautious pipeline must not be scored as a hallucinating one."""
    gateway, judge = gateway_for("1: yes")
    async with db_sessionmaker() as session:
        org_id = await _seed_org_with_answers(
            session,
            name="Refusal Co",
            answers=[(agent_prompts.REFUSAL_TEXT, []) for _ in range(5)],
        )

        result = await run_online_sampling(
            session, gateway, org_id, rng=random.Random(0), persist=False
        )

    assert result.scored == 0
    assert result.skipped_refusals == 5
    assert result.mean_faithfulness is None, "no measurement, not a zero"
    assert judge.calls == 0, "a refusal costs no judge call"


async def test_a_run_with_nothing_to_score_records_no_row(
    db_sessionmaker: Sessionmaker,
) -> None:
    """An empty row would enter the 7-day baseline as a real datapoint."""
    gateway, _ = gateway_for()
    async with db_sessionmaker() as session:
        org_id = await _seed_org_with_answers(session, name="Silent Co", answers=[])

        await run_online_sampling(session, gateway, org_id, rng=random.Random(0))
        await session.flush()

        rows = (await session.execute(select(EvalRun).where(EvalRun.org_id == org_id))).scalars()
        assert list(rows) == []
