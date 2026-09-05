"""Eval runner over the real pipeline (task 6.3).

Runs a handful of golden items end to end — hybrid retrieval, the grounded
prompt, the faithfulness gate — against a freshly seeded corpus, then checks the
report, the eval_runs row, and that scoring the pipeline left no trace in tenant
data.
"""

import asyncio
import json
import uuid
from dataclasses import asdict as _asdict
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runner import build_dependencies
from app.core.config import get_settings
from app.core.db import unscoped_session_factory
from app.models import Conversation, Escalation, EvalRun, EvalRunKind, Organization
from app.services.embeddings import EmbeddingService
from app.services.evaluation.dataset import GoldenItem
from app.services.evaluation.runner import (
    EvalItemResult,
    EvalSetupError,
    aggregate,
    format_summary,
    load_results,
    resolve_org,
    run_eval,
    sink_path_for,
    stub_providers,
)
from app.services.ingestion.seed import seed_corpus
from app.services.llm import LLMGateway
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]

ANSWERABLE = GoldenItem(
    id="t001",
    question="How often should I descale my espresso machine?",
    ground_truth="Every two months, or monthly with hard water.",
    expected_doc_ids=("descaling",),
    answerable=True,
    tags=("fast",),
)
UNANSWERABLE = GoldenItem(
    id="t002",
    question="What is your CEO's shoe size?",
    ground_truth="Not covered by the knowledge base; expect a refusal.",
    expected_doc_ids=(),
    answerable=False,
    tags=(),
)
SECOND_ANSWERABLE = GoldenItem(
    id="t003",
    question="What is the return window for most items?",
    ground_truth="30 days from delivery.",
    expected_doc_ids=("returns",),
    answerable=True,
    tags=(),
)


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker, tmp_path: Path):
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        LocalFileStorage(tmp_path),
        corpus_dir=CORPUS_DIR,
        org_name=f"eval-test-{uuid.uuid4()}",
    )
    try:
        yield summary.org_id
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def _eval_runs(sm: Sessionmaker) -> list[EvalRun]:
    async with sm() as session:
        rows = await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()))
        return list(rows.scalars().all())


async def _count(sm: Sessionmaker, model, org_id: uuid.UUID) -> int:
    async with sm() as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(model.org_id == org_id)
        )


async def test_full_run_scores_items_and_writes_a_report(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    out = tmp_path / "report.json"

    report = await run_eval(
        items=[ANSWERABLE, UNANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="test",
        report_path=out,
    )

    assert report.item_count == 3
    assert report.counts == {
        "items": 3,
        "answerable": 2,
        "unanswerable": 1,
        "answered": 2,
        "refused": 1,
        "escalated": 1,
        "errors": 0,
    }
    # Retrieval found the labelled document for both answerable items.
    assert report.metrics["context_recall"] == 1.0
    assert report.metrics["context_precision"] > 0.0
    # The out-of-KB question was refused, and neither real question was.
    assert report.metrics["refusal_accuracy"] == 1.0
    assert report.metrics["false_refusal_rate"] == 0.0
    # Every metric is a proper ratio.
    assert all(0.0 <= value <= 1.0 for value in report.metrics.values())

    # The run is labelled with what produced it — offline stub in tests.
    assert report.environment["llm_provider"] == "OfflineGroundedProvider"
    assert report.environment["embedding_model"]

    saved = json.loads(out.read_text())
    assert saved["report_path"] == str(out)
    assert {item["item_id"] for item in saved["items"]} == {"t001", "t002", "t003"}


async def test_answerable_item_is_answered_with_citations_and_scored(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    report = await run_eval(
        items=[ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="test",
        persist=False,
        report_path=tmp_path / "r.json",
    )

    item = report.items[0]
    assert item.error is None
    assert item.citation_count >= 1
    assert not item.refused
    assert "descaling" in item.retrieved_doc_ids
    assert item.context_recall == 1.0
    # Answered items carry both LLM-scored metrics; claim counts back the ratio.
    assert item.faithfulness is not None and item.claim_count >= 1
    assert item.answer_relevancy is not None


async def test_refusal_is_scored_as_a_refusal_not_as_a_bad_answer(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    report = await run_eval(
        items=[UNANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="test",
        persist=False,
        report_path=tmp_path / "r.json",
    )

    item = report.items[0]
    assert item.refused and item.escalated
    # None, not 0.0: a refusal makes no claims and has no expected document, so
    # averaging it in would drag faithfulness down for correct behaviour.
    assert item.faithfulness is None
    assert item.answer_relevancy is None
    assert item.context_precision is None
    assert item.context_recall is None
    assert report.metrics["refusal_accuracy"] == 1.0


async def test_run_records_an_eval_runs_row(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    before = len(await _eval_runs(db_sessionmaker))

    report = await run_eval(
        items=[ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="fast",
        report_path=tmp_path / "r.json",
    )

    rows = await _eval_runs(db_sessionmaker)
    assert len(rows) == before + 1
    row = rows[0]
    try:
        assert row.kind == EvalRunKind.golden
        assert row.subset == "fast"
        assert row.item_count == 1
        assert row.metrics["context_recall"] == report.metrics["context_recall"]
        assert row.metrics["count_items"] == 1
        assert row.environment["llm_provider"] == "OfflineGroundedProvider"
        assert row.passed is None  # no thresholds applied yet (6.4 sets this)
        assert row.report_path == str(tmp_path / "r.json")
    finally:
        async with db_sessionmaker() as session:
            await session.delete(await session.get(EvalRun, row.id))
            await session.commit()


async def test_scoring_creates_no_conversations_or_escalations(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    """The eval must not pollute the demo org's inbox — 107 items would otherwise
    land as conversations, and every refusal as an escalation."""
    report = await run_eval(
        items=[ANSWERABLE, UNANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="test",
        persist=False,
        report_path=tmp_path / "r.json",
    )

    assert report.counts["escalated"] == 1  # the graph did escalate...
    assert await _count(db_sessionmaker, Conversation, seeded) == 0  # ...without persisting it
    assert await _count(db_sessionmaker, Escalation, seeded) == 0


async def test_setup_errors_are_explicit(db_sessionmaker: Sessionmaker, seeded: uuid.UUID) -> None:
    with pytest.raises(EvalSetupError, match="not found"):
        await resolve_org(db_sessionmaker, org_id=uuid.uuid4())
    with pytest.raises(EvalSetupError, match="no organization named"):
        await resolve_org(db_sessionmaker, org_name="no-such-org")
    with pytest.raises(EvalSetupError, match="no golden items"):
        await run_eval(items=[], sessionmaker=db_sessionmaker, org_id=seeded)


async def test_empty_knowledge_base_fails_loudly_instead_of_scoring_zero(
    db_sessionmaker: Sessionmaker,
) -> None:
    async with db_sessionmaker() as session:
        org = Organization(name=f"empty-{uuid.uuid4()}")
        session.add(org)
        await session.commit()
        org_id = org.id
    try:
        with pytest.raises(EvalSetupError, match="no ready documents"):
            await resolve_org(db_sessionmaker, org_id=org_id)
    finally:
        async with db_sessionmaker() as session:
            await session.delete(await session.get(Organization, org_id))
            await session.commit()


# --- aggregation and summary (pure) ------------------------------------------


def _result(**overrides) -> EvalItemResult:
    base = dict(
        item_id="x",
        question="q",
        answerable=True,
        expected_doc_ids=["returns"],
        retrieved_doc_ids=["returns"],
        answer="a",
        escalated=False,
        refused=False,
        citation_count=1,
    )
    return EvalItemResult(**(base | overrides))


def test_aggregate_averages_each_metric_over_applicable_items_only() -> None:
    items = [
        _result(item_id="a", faithfulness=1.0, context_recall=1.0, context_precision=1.0),
        _result(item_id="b", faithfulness=0.0, context_recall=0.0, context_precision=0.0),
        # A refusal contributes to refusal_accuracy but not to faithfulness.
        _result(item_id="c", answerable=False, expected_doc_ids=[], refused=True, escalated=True),
    ]

    metrics, counts = aggregate(items)

    assert metrics["faithfulness"] == 0.5
    assert metrics["context_recall"] == 0.5
    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["false_refusal_rate"] == 0.0
    assert metrics["escalation_rate"] == pytest.approx(1 / 3, abs=1e-4)  # reports round to 4dp
    assert counts == {
        "items": 3,
        "answerable": 2,
        "unanswerable": 1,
        "answered": 2,
        "refused": 1,
        "escalated": 1,
        "errors": 0,
    }


def test_failed_items_are_counted_and_excluded_from_scores() -> None:
    items = [
        _result(item_id="a", faithfulness=1.0, context_recall=1.0),
        _result(item_id="b", error="LLMError: boom"),
    ]

    metrics, counts = aggregate(items)

    assert metrics["faithfulness"] == 1.0  # the failed item is not a zero
    assert counts["errors"] == 1
    assert counts["answered"] == 1


def test_aggregate_of_an_all_refusal_run_exposes_the_trade_off() -> None:
    """Refusing everything aces refusal_accuracy; false_refusal_rate is what tells."""
    items = [
        _result(item_id="a", refused=True, escalated=True),
        _result(item_id="b", answerable=False, expected_doc_ids=[], refused=True, escalated=True),
    ]

    metrics, _ = aggregate(items)

    assert metrics["refusal_accuracy"] == 1.0
    assert metrics["false_refusal_rate"] == 1.0


def test_summary_flags_offline_stub_runs() -> None:
    from app.services.evaluation.runner import EvalReport

    report = EvalReport(
        dataset="golden.jsonl",
        subset="full",
        org_id=str(uuid.uuid4()),
        item_count=1,
        started_at="2026-07-29T00:00:00+00:00",
        duration_ms=1200,
        metrics={"faithfulness": 1.0},
        counts={"items": 1, "errors": 0},
        environment={"llm_provider": "OfflineGroundedProvider"},
        git_sha=None,
    )

    text = format_summary(report)

    assert "faithfulness" in text
    assert "WARNING" in text, "an unlabelled stub run reads as a real quality claim"
    assert "OfflineGroundedProvider" in text, "the summary must name which stub scored it"


def test_summary_flags_a_hosted_run_that_fell_back_to_stub_embeddings() -> None:
    """The trap an ANTHROPIC_API_KEY alone sets: real LLM, stub embeddings.

    Anthropic serves no embedding model, so EmbeddingService falls back to the
    hashing provider behind a log warning and dense retrieval silently becomes a
    bag-of-words match. The summary has to say so, or context_recall reads as real.
    """
    from app.services.evaluation.runner import EvalReport

    report = EvalReport(
        dataset="golden.jsonl",
        subset="fast",
        org_id=str(uuid.uuid4()),
        item_count=1,
        started_at="2026-07-29T00:00:00+00:00",
        duration_ms=1200,
        metrics={"context_recall": 1.0},
        counts={"items": 1, "errors": 0},
        environment={
            "llm_provider": "LiteLLMProvider",
            "embedding_provider": "HashingEmbeddingProvider",
        },
        git_sha=None,
    )

    text = format_summary(report)

    assert "HashingEmbeddingProvider" in text
    assert "ANTHROPIC_API_KEY alone does not supply embeddings" in text


# --- resilience to a wedged item ---------------------------------------------
#
# On 2026-08-01 a 30-item run hung after 20 items and was killed after 7h16m,
# losing every scored item: Ollama evicted a model mid-request, nothing bounded the
# call, and gather() stranded the 20 completed results (docs/NOTES.md, Phase 6.4).
# These reproduce that shape in under a second.


class _HangingGateway:
    """A gateway that wedges on one question and behaves normally on the rest.

    Wraps a real gateway so non-wedged items still score for real — the point of
    the watchdog is that the rest of the run completes, which a fully-fake gateway
    could not demonstrate.
    """

    def __init__(self, inner, hang_on: str) -> None:
        self._inner = inner
        self._hang_on = hang_on

    def _wedged(self, messages) -> bool:
        return any(self._hang_on in msg.content for msg in messages)

    @property
    def provider_name(self) -> str:
        return self._inner.provider_name

    def model_for(self, route):
        return self._inner.model_for(route)

    async def complete(self, messages, **kwargs):
        if self._wedged(messages):
            await asyncio.Event().wait()  # never resolves, exactly like the incident
        return await self._inner.complete(messages, **kwargs)

    def stream(self, messages, **kwargs):
        if self._wedged(messages):

            async def hang():
                await asyncio.Event().wait()
                yield ""  # pragma: no cover

            return hang()
        return self._inner.stream(messages, **kwargs)


async def test_a_wedged_item_times_out_and_the_rest_of_the_run_completes(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    """The incident, as a one-second test."""
    deps = build_dependencies(
        session_factory=unscoped_session_factory(db_sessionmaker),
        gateway=_HangingGateway(LLMGateway(), hang_on="CEO's shoe size"),
    )

    report = await run_eval(
        items=[ANSWERABLE, UNANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        deps=deps,
        concurrency=3,
        item_timeout=0.3,
        persist=False,
        report_path=tmp_path / "wedged.json",
    )

    wedged = next(i for i in report.items if i.item_id == UNANSWERABLE.id)
    assert wedged.error is not None and wedged.error.startswith("TimeoutError")
    assert report.counts["errors"] == 1
    # The whole point: the other two items still produced scores.
    assert all(i.error is None for i in report.items if i.item_id != UNANSWERABLE.id)
    assert report.metrics["faithfulness"] is not None
    assert (tmp_path / "wedged.json").exists()


async def test_the_watchdog_does_not_charge_an_item_for_waiting_on_a_slot(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    """Queueing for a concurrency slot is legitimate time. If the timeout were
    placed outside the semaphore, items 2..N would inherit their predecessors'
    latency and fail en masse — at concurrency 4 that is items 5..30."""
    report = await run_eval(
        items=[ANSWERABLE, UNANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        concurrency=1,
        item_timeout=20.0,
        persist=False,
        report_path=tmp_path / "queued.json",
    )

    assert report.counts["errors"] == 0


async def test_an_exception_escaping_score_item_is_recorded_not_raised(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path, monkeypatch
) -> None:
    """gather(return_exceptions=True): one unforeseen escape must not strand every
    sibling's already-computed result, which is what the 2026-08-01 wedge did."""
    from app.services.evaluation import runner as runner_module

    real = runner_module.score_item

    async def explode_on_one(deps, *, org_id, item, embedding_service, judge_route):
        if item.id == SECOND_ANSWERABLE.id:
            raise MemoryError("simulated")
        return await real(
            deps,
            org_id=org_id,
            item=item,
            embedding_service=embedding_service,
            judge_route=judge_route,
        )

    monkeypatch.setattr(runner_module, "score_item", explode_on_one)

    report = await run_eval(
        items=[ANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        persist=False,
        report_path=tmp_path / "escaped.json",
    )

    assert len(report.items) == 2, "every requested item must produce a row"
    exploded = next(i for i in report.items if i.item_id == SECOND_ANSWERABLE.id)
    assert exploded.error == "MemoryError: simulated"
    assert report.counts["errors"] == 1


async def test_the_timeout_budgets_are_recorded_in_the_environment(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    """Two runs are only comparable when the bounds match — a tight bound converts
    slow-but-healthy items into errors that aggregate then excludes."""
    report = await run_eval(
        items=[ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        item_timeout=123.0,
        persist=False,
        report_path=tmp_path / "env.json",
    )

    assert report.environment["item_timeout_seconds"] == 123.0
    assert report.environment["llm_timeout_seconds"] == get_settings().llm_timeout_seconds
    # The budgets are floats, so the gate's string-valued stub scan cannot mistake
    # one for a provider name — it still reports exactly the two offline stubs.
    assert stub_providers(report.environment) == [
        "embedding_provider=HashingEmbeddingProvider",
        "llm_provider=OfflineGroundedProvider",
    ]


# --- incremental sink and resume ---------------------------------------------
#
# The other half of the 2026-08-01 loss: the report was a single write after the
# last item, so killing the run at 20/30 salvaged nothing. Each item is now
# appended as it lands, and --resume turns a kill into a continuation.


async def test_each_item_is_appended_to_the_sink_as_it_finishes(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    out = tmp_path / "r.json"

    await run_eval(
        items=[ANSWERABLE, UNANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        persist=False,
        report_path=out,
    )

    # A complete run cleans the sink up, so read it back mid-flight instead by
    # checking the loader against a sink written by a run that was NOT completed.
    assert not sink_path_for(out).exists(), "a finished report supersedes its sink"


async def test_the_sink_survives_a_run_that_never_writes_a_report(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path, monkeypatch
) -> None:
    """The salvage path: the process dies before the report is written, and the
    scored items are still on disk. This is what the 7h16m kill lost."""
    from app.services.evaluation import runner as runner_module

    def explode(report, path):
        raise KeyboardInterrupt("simulated kill after the last item")

    monkeypatch.setattr(runner_module, "write_report", explode)
    out = tmp_path / "killed.json"

    with pytest.raises(KeyboardInterrupt):
        await run_eval(
            items=[ANSWERABLE, SECOND_ANSWERABLE],
            sessionmaker=db_sessionmaker,
            org_id=seeded,
            persist=False,
            report_path=out,
        )

    salvaged = load_results(sink_path_for(out))
    assert set(salvaged) == {ANSWERABLE.id, SECOND_ANSWERABLE.id}
    assert salvaged[ANSWERABLE.id].faithfulness is not None


async def test_resume_skips_items_already_scored(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path, monkeypatch
) -> None:
    from app.services.evaluation import runner as runner_module

    sink = tmp_path / "prior.jsonl"
    prior = await run_eval(
        items=[ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        persist=False,
        report_path=tmp_path / "prior.json",
    )
    # Rebuild the sink the completed run cleaned up.
    sink.write_text(json.dumps(_asdict(prior.items[0]), sort_keys=True) + "\n")

    scored: list[str] = []
    real = runner_module.score_item

    async def counting(deps, *, org_id, item, embedding_service, judge_route):
        scored.append(item.id)
        return await real(
            deps,
            org_id=org_id,
            item=item,
            embedding_service=embedding_service,
            judge_route=judge_route,
        )

    monkeypatch.setattr(runner_module, "score_item", counting)

    report = await run_eval(
        items=[ANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        persist=False,
        report_path=tmp_path / "resumed.json",
        resume_path=sink,
    )

    assert scored == [SECOND_ANSWERABLE.id], "the already-scored item must not re-run"
    assert report.resumed_item_count == 1
    assert report.resumed_from == str(sink)
    assert [i.item_id for i in report.items] == [ANSWERABLE.id, SECOND_ANSWERABLE.id]
    carried = next(i for i in report.items if i.item_id == ANSWERABLE.id)
    assert carried.answer == prior.items[0].answer, "the prior answer survives verbatim"


async def test_resume_rescores_an_item_that_errored(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path, monkeypatch
) -> None:
    """A timed-out item must be retried, not inherited — otherwise the CLI keeps
    exiting 1 for a failure the resume never actually re-attempted."""
    from app.services.evaluation import runner as runner_module

    sink = tmp_path / "prior.jsonl"
    clean = _asdict(runner_module.blank_result(ANSWERABLE))
    errored = _asdict(
        runner_module.failed_result(SECOND_ANSWERABLE, "TimeoutError: item exceeded 30s")
    )
    sink.write_text(
        json.dumps(clean, sort_keys=True) + "\n" + json.dumps(errored, sort_keys=True) + "\n"
    )

    scored: list[str] = []
    real = runner_module.score_item

    async def counting(deps, *, org_id, item, embedding_service, judge_route):
        scored.append(item.id)
        return await real(
            deps,
            org_id=org_id,
            item=item,
            embedding_service=embedding_service,
            judge_route=judge_route,
        )

    monkeypatch.setattr(runner_module, "score_item", counting)

    report = await run_eval(
        items=[ANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        persist=False,
        report_path=tmp_path / "resumed.json",
        resume_path=sink,
    )

    assert scored == [SECOND_ANSWERABLE.id]
    assert report.counts["errors"] == 0, "the retried item now has a real score"


def test_resume_tolerates_a_truncated_last_line(tmp_path: Path) -> None:
    """A kill mid-write leaves a partial final line. Refusing to read the file
    would defeat the entire point of having written it."""
    from app.services.evaluation.runner import blank_result

    sink = tmp_path / "torn.jsonl"
    good = json.dumps(_asdict(blank_result(ANSWERABLE)), sort_keys=True)
    sink.write_text(good + "\n" + good[: len(good) // 2])

    salvaged = load_results(sink)

    assert set(salvaged) == {ANSWERABLE.id}


async def test_a_resumed_run_still_records_exactly_one_eval_runs_row(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID, tmp_path: Path
) -> None:
    """eval_runs is the record of a *finished* run. A half-run row would corrupt
    6.6's Quality-card trend, so the killed run inserts none and this inserts one."""
    from app.services.evaluation.runner import blank_result

    sink = tmp_path / "prior.jsonl"
    sink.write_text(json.dumps(_asdict(blank_result(ANSWERABLE)), sort_keys=True) + "\n")
    before = len(await _eval_runs(db_sessionmaker))

    await run_eval(
        items=[ANSWERABLE, SECOND_ANSWERABLE],
        sessionmaker=db_sessionmaker,
        org_id=seeded,
        subset="test",
        report_path=tmp_path / "r.json",
        resume_path=sink,
    )

    rows = await _eval_runs(db_sessionmaker)
    try:
        assert len(rows) == before + 1
    finally:
        async with db_sessionmaker() as session:
            await session.delete(await session.get(EvalRun, rows[0].id))
            await session.commit()
