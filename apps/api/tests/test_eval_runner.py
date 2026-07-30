"""Eval runner over the real pipeline (task 6.3).

Runs a handful of golden items end to end — hybrid retrieval, the grounded
prompt, the faithfulness gate — against a freshly seeded corpus, then checks the
report, the eval_runs row, and that scoring the pipeline left no trace in tenant
data.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation, Escalation, EvalRun, EvalRunKind, Organization
from app.services.embeddings import EmbeddingService
from app.services.evaluation.dataset import GoldenItem
from app.services.evaluation.runner import (
    EvalItemResult,
    EvalSetupError,
    aggregate,
    format_summary,
    resolve_org,
    run_eval,
)
from app.services.ingestion.seed import seed_corpus
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
    assert "offline stub" in text, "an unlabelled stub run reads as a real quality claim"
