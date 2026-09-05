"""Run the golden set through the real pipeline and score it (plan 6.3).

Fidelity is the point: each item goes through ``app.agent.runner.run_turn``, the
same graph the chat endpoint serves — router, hybrid retrieval, the grounded
prompt, the faithfulness gate. Nothing is stubbed or shortcut, so a regression in
any of them shows up in the report.

The run deliberately passes no ``conversation_id``, which keeps 107 synthetic
conversations and their escalations out of the demo org's inbox (the ``escalate``
node skips its write when state carries no conversation).
"""

import asyncio
import datetime as dt
import json
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.runner import build_dependencies, run_turn
from app.agent.state import AgentDependencies
from app.core.config import get_settings
from app.core.db import unscoped_session_factory
from app.core.logging import get_logger
from app.models import Document, DocumentStatus, EvalRun, EvalRunKind, Organization
from app.services.embeddings import EmbeddingService
from app.services.evaluation import metrics as m
from app.services.evaluation.dataset import GoldenItem
from app.services.evaluation.gate import GateResult, Threshold, evaluate_gate
from app.services.ingestion.seed import DEMO_ORG_NAME
from app.services.llm import LLMRoute
from app.services.tracing import turn_trace

logger = get_logger(__name__)

DEFAULT_CONCURRENCY = 4
REPORTS_DIR = Path(__file__).resolve().parents[5] / "eval" / "reports"


class EvalSetupError(Exception):
    """The corpus the golden set describes is not in the database."""


@dataclass
class EvalItemResult:
    item_id: str
    question: str
    answerable: bool
    expected_doc_ids: list[str]
    retrieved_doc_ids: list[str]
    answer: str
    escalated: bool
    refused: bool
    citation_count: int
    #: The pipeline's own judge score (0-1), for comparison with the independent
    #: eval judge below. Absent when the answer never reached the judge.
    pipeline_confidence: float | None = None
    #: RAGAS-style scores. None means "not applicable to this item", never zero —
    #: a refusal has no claims to be faithful about, and an unanswerable item has
    #: no expected document to be precise about.
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    claim_count: int = 0
    supported_claim_count: int = 0
    latency_ms: int = 0
    trace_id: str | None = None
    error: str | None = None


@dataclass
class EvalReport:
    dataset: str
    subset: str
    org_id: str
    item_count: int
    started_at: str
    duration_ms: int
    #: None for a metric whose population was empty — "not measured", never 0.0.
    metrics: dict[str, float | None]
    counts: dict[str, int]
    environment: dict[str, Any]
    git_sha: str | None
    items: list[EvalItemResult] = field(default_factory=list)
    report_path: str | None = None
    #: Gate verdict when thresholds were applied (6.4); None when they were not.
    #: Kept on the report so the JSON artifact records what CI decided and why.
    gate: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Providers that fabricate plausible-looking scores without a model behind them.
#: A gated run must never pass on these — see ``stub_providers``.
STUB_PROVIDERS = frozenset({"OfflineGroundedProvider", "HashingEmbeddingProvider"})


def stub_providers(environment: dict[str, Any]) -> list[str]:
    """Which of a run's providers were deterministic stubs rather than models.

    The gate refuses to pass when this is non-empty. Both stubs are lexical, so
    they produce healthy-looking numbers from a pipeline that was never exercised:
    the answer stub always cites, and the hashing embedder makes dense retrieval a
    bag-of-words match. Green on stub scores is the worst outcome a gate can have.
    """
    return sorted(
        f"{key}={value}"
        for key, value in environment.items()
        if isinstance(value, str) and value in STUB_PROVIDERS
    )


def environment_labels(
    deps: AgentDependencies,
    judge_route: LLMRoute = m.JUDGE_ROUTE,
    embedding_service: EmbeddingService | None = None,
    item_timeout: float | None = None,
) -> dict[str, Any]:
    """What produced the scores. A run without this is an unreadable number.

    In particular ``llm_provider: OfflineGroundedProvider`` marks a run scored by
    the deterministic offline stub — useful to validate the harness, never a
    publishable quality claim. ``judge_model`` matters just as much: the same
    pipeline scores materially differently under a 3B and a 7B judge, so two runs
    are only comparable when this line matches.
    """
    settings = get_settings()
    return {
        "llm_provider": deps.gateway.provider_name,
        "cheap_model": deps.gateway.model_for(LLMRoute.cheap),
        "strong_model": deps.gateway.model_for(LLMRoute.strong),
        "judge_model": deps.gateway.model_for(judge_route),
        "embedding_provider": (
            embedding_service.provider_name if embedding_service is not None else None
        ),
        "embedding_model": settings.embedding_model,
        "embedding_dims": settings.embedding_dims,
        "reranker": settings.reranker,
        "retrieval_top_n": deps.retrieval_top_n,
        "faithfulness_threshold": deps.faithfulness_threshold,
        # Same argument as judge_model: a run scored under a 60s call bound is not
        # the same experiment as one under 600s, because the tight bound converts
        # slow-but-healthy items into errors that aggregate then excludes.
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "item_timeout_seconds": item_timeout,
    }


def git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


async def resolve_org(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    org_id: uuid.UUID | None = None,
    org_name: str = DEMO_ORG_NAME,
) -> uuid.UUID:
    """Find the seeded org and refuse to score an empty knowledge base.

    Every metric would be a silent zero against an org with no ready documents,
    which reads as "the pipeline is broken" rather than "you forgot to seed".
    """
    async with sessionmaker() as session:
        if org_id is None:
            found = (
                await session.execute(select(Organization).where(Organization.name == org_name))
            ).scalars()
            orgs = list(found)
            if not orgs:
                raise EvalSetupError(
                    f"no organization named {org_name!r}; run scripts/seed.py first"
                )
            if len(orgs) > 1:
                raise EvalSetupError(
                    f"{len(orgs)} organizations named {org_name!r}; pass an explicit org id"
                )
            org_id = orgs[0].id
        elif await session.get(Organization, org_id) is None:
            raise EvalSetupError(f"organization {org_id} not found")

        ready = (
            await session.execute(
                select(Document.title).where(
                    Document.org_id == org_id, Document.status == DocumentStatus.ready
                )
            )
        ).scalars()
        titles = set(ready)
    if not titles:
        raise EvalSetupError(f"organization {org_id} has no ready documents; run scripts/seed.py")
    logger.info("eval_org_resolved", org_id=str(org_id), ready_documents=len(titles))
    return org_id


def missing_documents(items: Sequence[GoldenItem], available_titles: set[str]) -> set[str]:
    """Golden-set documents absent from the org, i.e. guaranteed recall misses."""
    expected = {slug for item in items for slug in item.expected_doc_ids}
    return expected - available_titles


def blank_result(item: GoldenItem) -> EvalItemResult:
    """An unscored result skeleton for ``item``.

    Shared by ``score_item`` and ``failed_result`` so an item that never ran and one
    that ran and failed are the same shape — ``aggregate`` excludes both from every
    average on ``error is not None`` alone.
    """
    return EvalItemResult(
        item_id=item.id,
        question=item.question,
        answerable=item.answerable,
        expected_doc_ids=list(item.expected_doc_ids),
        retrieved_doc_ids=[],
        answer="",
        escalated=False,
        refused=False,
        citation_count=0,
    )


def failed_result(item: GoldenItem, error: str) -> EvalItemResult:
    """A result recording that ``item`` could not be scored, and why."""
    result = blank_result(item)
    result.error = error
    return result


async def score_item(
    deps: AgentDependencies,
    *,
    org_id: uuid.UUID,
    item: GoldenItem,
    embedding_service: EmbeddingService,
    judge_route: LLMRoute = m.JUDGE_ROUTE,
) -> EvalItemResult:
    result = blank_result(item)
    started = time.perf_counter()
    # The item id doubles as the Langfuse session so a run's traces group together
    # and per-item scores land next to the turn that produced them.
    with turn_trace(
        "eval.item",
        org_id=str(org_id),
        conversation_id=item.id,
        input={"question": item.question},
        metadata={"answerable": item.answerable, "expected_doc_ids": list(item.expected_doc_ids)},
        tags=["eval", "golden"],
    ) as trace:
        result.trace_id = trace.trace_id
        try:
            state = await run_turn(deps, org_id=org_id, question=item.question)
        except Exception as exc:  # noqa: BLE001 - one bad item must not void the run
            result.error = f"{type(exc).__name__}: {exc}"
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("eval_item_failed", item_id=item.id, error=result.error)
            return result

        chunks = state.get("chunks", []) or []
        # chunk document_title is the corpus slug: seed_corpus titles each
        # document path.stem, and golden.jsonl references those same stems.
        result.retrieved_doc_ids = [chunk["document_title"] for chunk in chunks]
        result.answer = state.get("response") or state.get("answer") or ""
        result.escalated = bool(state.get("escalated"))
        result.citation_count = len(state.get("citations") or [])
        result.pipeline_confidence = state.get("confidence")
        result.refused = m.is_refusal(result.answer, escalated=result.escalated)

        if item.answerable:
            result.context_precision = m.context_precision(
                result.retrieved_doc_ids, item.expected_doc_ids
            )
            result.context_recall = m.context_recall(
                result.retrieved_doc_ids, item.expected_doc_ids
            )
        if not result.refused:
            # Only a real answer can be unfaithful or irrelevant; scoring a
            # refusal here would punish the pipeline for behaving correctly.
            answer_text = state.get("answer") or result.answer
            # Scoring calls a model too, so it fails the same ways the pipeline does
            # (a 429 on an exhausted quota, a 503 under load). This needs its own
            # guard: without it a metric failure escapes the per-item except above
            # and aborts the whole run, losing every item already scored.
            try:
                faith = await m.faithfulness(deps.gateway, answer_text, chunks, route=judge_route)
                result.faithfulness = faith.score
                result.claim_count = len(faith.claims)
                result.supported_claim_count = faith.supported_count
                result.answer_relevancy = await m.answer_relevancy(
                    deps.gateway, embedding_service, item.question, answer_text, route=judge_route
                )
            except Exception as exc:  # noqa: BLE001 - one bad item must not void the run
                result.error = f"scoring failed: {type(exc).__name__}: {exc}"
                logger.warning("eval_item_scoring_failed", item_id=item.id, error=result.error)

        trace.update(
            output={"answer": result.answer, "refused": result.refused},
            metadata={
                "faithfulness": result.faithfulness,
                "context_recall": result.context_recall,
            },
        )
        for name, value in (
            ("eval_faithfulness", result.faithfulness),
            ("eval_answer_relevancy", result.answer_relevancy),
            ("eval_context_precision", result.context_precision),
            ("eval_context_recall", result.context_recall),
        ):
            if value is not None:
                trace.score(name, value)

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def aggregate(
    items: Sequence[EvalItemResult],
) -> tuple[dict[str, float | None], dict[str, int]]:
    """Aggregate per-item scores, averaging each metric only over applicable items."""
    answerable = [i for i in items if i.answerable]
    unanswerable = [i for i in items if not i.answerable]
    scored = [i for i in items if i.error is None]

    def collect(attr: str, population: Sequence[EvalItemResult]) -> list[float]:
        return [
            value for i in population if (value := getattr(i, attr)) is not None and i.error is None
        ]

    # mean_or_none throughout: every one of these averages over a population that a
    # subset can leave empty, and None ("not measured") must not be reported as 0.0
    # ("measured, scored zero") — the gate treats the two very differently.
    metrics = {
        "faithfulness": m.mean_or_none(collect("faithfulness", items)),
        "answer_relevancy": m.mean_or_none(collect("answer_relevancy", items)),
        "context_precision": m.mean_or_none(collect("context_precision", answerable)),
        "context_recall": m.mean_or_none(collect("context_recall", answerable)),
        # The refusal pair reads as a trade-off: refusing everything would ace
        # refusal_accuracy, and false_refusal_rate is what exposes that.
        "refusal_accuracy": (
            m.mean_or_none([1.0 if i.refused else 0.0 for i in unanswerable if i.error is None])
        ),
        "false_refusal_rate": (
            m.mean_or_none([1.0 if i.refused else 0.0 for i in answerable if i.error is None])
        ),
        "escalation_rate": m.mean_or_none([1.0 if i.escalated else 0.0 for i in scored]),
    }
    counts = {
        "items": len(items),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "answered": sum(1 for i in scored if not i.refused),
        "refused": sum(1 for i in scored if i.refused),
        "escalated": sum(1 for i in scored if i.escalated),
        "errors": len(items) - len(scored),
    }
    rounded = {k: (None if v is None else round(v, 4)) for k, v in metrics.items()}
    return rounded, counts


async def run_eval(
    *,
    items: Sequence[GoldenItem],
    sessionmaker: async_sessionmaker[AsyncSession],
    org_id: uuid.UUID | None = None,
    org_name: str = DEMO_ORG_NAME,
    dataset: str = "golden.jsonl",
    subset: str = "full",
    concurrency: int = DEFAULT_CONCURRENCY,
    deps: AgentDependencies | None = None,
    embedding_service: EmbeddingService | None = None,
    judge_route: LLMRoute = m.JUDGE_ROUTE,
    thresholds: Sequence[Threshold] | None = None,
    persist: bool = True,
    report_path: Path | None = None,
    item_timeout: float | None = None,
) -> EvalReport:
    """Score ``items`` against the pipeline, write the report, record the run."""
    if not items:
        raise EvalSetupError("no golden items selected")
    resolved_org = await resolve_org(sessionmaker, org_id=org_id, org_name=org_name)

    embedding_service = embedding_service or EmbeddingService()
    # The agent reads through the caller's sessionmaker rather than the module
    # global in app.core.db: one engine per run, on the caller's event loop.
    deps = deps or build_dependencies(
        session_factory=unscoped_session_factory(sessionmaker),
        embedding_service=embedding_service,
    )

    started_at = dt.datetime.now(dt.UTC)
    started = time.perf_counter()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    # A scored item makes at most three strong-route calls (streamed answer,
    # faithfulness, answer_relevancy) plus two cheap ones, each already bounded by
    # LLM_TIMEOUT_SECONDS. Their sum is the natural item bound — so this watchdog can
    # only fire when that per-call bound has already failed to, which is its whole job.
    resolved_item_timeout = (
        item_timeout if item_timeout is not None else 3 * get_settings().llm_timeout_seconds
    )

    async def bounded(item: GoldenItem) -> EvalItemResult:
        # The timeout goes INSIDE the semaphore: time spent queueing for a
        # concurrency slot is legitimate, and charging it to the item would fail
        # items 5..N en masse at concurrency 4. It goes OUTSIDE score_item because
        # asyncio.timeout cancels by injecting CancelledError, a BaseException that
        # score_item's two `except Exception` guards deliberately cannot absorb — so
        # the TimeoutError surfaces here, at exactly one place.
        async with semaphore:
            try:
                async with asyncio.timeout(resolved_item_timeout):
                    return await score_item(
                        deps,
                        org_id=resolved_org,
                        item=item,
                        embedding_service=embedding_service,
                        judge_route=judge_route,
                    )
            except TimeoutError:
                logger.warning(
                    "eval_item_timed_out", item_id=item.id, seconds=resolved_item_timeout
                )
                return failed_result(
                    item, f"TimeoutError: item exceeded {resolved_item_timeout:.0f}s"
                )

    # return_exceptions so one unforeseen escape (a MemoryError, a bug in a guard)
    # cannot strand every sibling's already-computed result the way the 2026-08-01
    # wedge did — every requested item produces a row, and counts["errors"] is honest.
    outcomes = await asyncio.gather(*(bounded(item) for item in items), return_exceptions=True)
    results = [
        outcome
        if isinstance(outcome, EvalItemResult)
        else failed_result(item, f"{type(outcome).__name__}: {outcome}")
        for item, outcome in zip(items, outcomes, strict=True)
    ]
    duration_ms = int((time.perf_counter() - started) * 1000)

    metrics, counts = aggregate(results)
    report = EvalReport(
        dataset=dataset,
        subset=subset,
        org_id=str(resolved_org),
        item_count=len(results),
        started_at=started_at.isoformat(),
        duration_ms=duration_ms,
        metrics=metrics,
        counts=counts,
        environment=environment_labels(
            deps, judge_route, embedding_service, item_timeout=resolved_item_timeout
        ),
        git_sha=git_sha(),
        items=list(results),
    )

    # The gate runs before the report is written and the row recorded, so both
    # artifacts carry the verdict rather than leaving CI as the only place it exists.
    gate_result = evaluate_gate(metrics, thresholds) if thresholds is not None else None
    if gate_result is not None:
        report.gate = gate_summary(gate_result)

    target = report_path or default_report_path(started_at, subset)
    if target is not None:
        report.report_path = str(write_report(report, target))
    if persist:
        await record_run(sessionmaker, report, passed=gate_result.passed if gate_result else None)
    logger.info(
        "eval_run_complete",
        subset=subset,
        duration_ms=duration_ms,
        **{k: v for k, v in metrics.items() if v is not None},
    )
    return report


def gate_summary(result: GateResult) -> dict[str, Any]:
    """Flatten a gate verdict for the JSON report — thresholds included, so a stored
    report explains itself even after the defaults move."""
    return {
        "passed": result.passed,
        "checks": [
            {
                "metric": check.threshold.metric,
                "score": check.score,
                "bound": check.threshold.bound,
                "threshold": check.threshold.value,
                "advisory": check.threshold.advisory,
                "passed": check.passed,
            }
            for check in result.checks
        ],
    }


def default_report_path(started_at: dt.datetime, subset: str) -> Path:
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    return REPORTS_DIR / f"eval-{stamp}-{subset}.json"


def write_report(report: EvalReport, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["report_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


async def record_run(
    sessionmaker: async_sessionmaker[AsyncSession],
    report: EvalReport,
    *,
    passed: bool | None = None,
) -> uuid.UUID:
    """Insert the eval_runs row 6.4's gate and 6.6's Quality card read."""
    async with sessionmaker() as session:
        run = EvalRun(
            kind=EvalRunKind.golden,
            dataset=report.dataset,
            subset=report.subset,
            item_count=report.item_count,
            metrics=report.metrics | {f"count_{k}": v for k, v in report.counts.items()},
            environment=report.environment,
            passed=passed,
            duration_ms=report.duration_ms,
            git_sha=report.git_sha,
            report_path=report.report_path,
        )
        session.add(run)
        await session.commit()
        return run.id


def format_summary(report: EvalReport) -> str:
    """The table the CLI prints. Metric names match the plan's gate thresholds."""
    lines = [
        f"Eval run — dataset={report.dataset} subset={report.subset} items={report.item_count}",
        f"  org={report.org_id}  duration={report.duration_ms / 1000:.1f}s",
        "",
        f"  {'metric':<22}{'score':>8}",
        f"  {'-' * 30}",
    ]
    lines += [
        f"  {name:<22}{'not measured' if value is None else format(value, '.3f'):>12}"
        for name, value in report.metrics.items()
    ]
    lines += [
        "",
        "  " + "  ".join(f"{name}={value}" for name, value in report.counts.items()),
        "",
        "  environment: " + ", ".join(f"{k}={v}" for k, v in report.environment.items()),
    ]
    stubs = stub_providers(report.environment)
    if stubs:
        lines += [
            "",
            f"  WARNING: scored with deterministic stubs, not models ({', '.join(stubs)}).",
            "  These numbers validate the harness only. Start Ollama or set an API key",
            "  for scores worth quoting.",
        ]
        if "HashingEmbeddingProvider" in " ".join(stubs):
            lines.append(
                "  Note: an ANTHROPIC_API_KEY alone does not supply embeddings — "
                "Anthropic\n  serves no embedding model, so retrieval degraded to "
                "lexical hashing."
            )
    if report.counts.get("errors"):
        lines.append(f"\n  {report.counts['errors']} item(s) failed — see the JSON report.")
    if report.report_path:
        lines.append(f"\n  report: {report.report_path}")
    return "\n".join(lines)
