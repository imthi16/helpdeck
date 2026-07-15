"""RAGAS-style evaluation runner over the golden dataset (task 6.3).

Runs the real pipeline in-process (seed an ephemeral org -> ingest the corpus
-> ``run_turn`` per golden item) and computes:

- Deterministic metrics (always): context_recall / context_precision from
  ``expected_doc_ids`` vs the retrieved chunks' docs, refusal_accuracy on the
  unanswerable subset, citation_validity, answered_rate.
- RAGAS metrics (``--ragas``): faithfulness and answer_relevancy judged by a
  local Ollama model via ragas + langchain-ollama.

Writes a JSON report to ``eval/reports/`` and a row into ``eval_runs``, prints
a summary table, and (with ``--gate``) exits non-zero when thresholds fail.

Usage (repo root):
    uv run --project apps/api --group eval python eval/run_eval.py \
        --subset fast --no-ragas --gate
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import types
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

# Settings read `.env` relative to the working directory; the API's env file
# lives in apps/api. All report paths below are absolute, so this is safe.
import os  # noqa: E402

os.chdir(API_ROOT)

GOLDEN = REPO_ROOT / "eval" / "golden.jsonl"
CORPUS = REPO_ROOT / "eval" / "fixtures" / "corpus"
REPORTS = REPO_ROOT / "eval" / "reports"

# Default gate thresholds. citation_validity stands in for judged faithfulness
# on the PR gate (a weakened grounded prompt loses citations); the judged
# faithfulness threshold is enforced only when RAGAS runs (nightly/local).
THRESHOLDS = {
    "context_recall": 0.70,
    "refusal_accuracy": 0.90,
    "citation_validity": 0.85,
}


def _shim_ragas_imports() -> None:
    """ragas 0.4 imports vertexai symbols that langchain-community 0.4 removed.

    They are only used for isinstance checks against providers we never use,
    so stub the two modules before importing ragas.
    """
    for name, cls in (
        ("langchain_community.chat_models.vertexai", "ChatVertexAI"),
        ("langchain_community.embeddings.vertexai", "VertexAIEmbeddings"),
    ):
        if name not in sys.modules:
            module = types.ModuleType(name)
            setattr(module, cls, type(cls, (), {}))
            sys.modules[name] = module


@dataclass
class ItemResult:
    id: str
    question: str
    answerable: bool
    expected_docs: list[str]
    answer: str = ""
    escalated: bool = False
    retrieved_docs: list[str] = field(default_factory=list)
    contexts: list[str] = field(default_factory=list)
    citations: int = 0
    ground_truth: str = ""
    error: str | None = None


def load_items(subset: str, limit: int | None) -> list[dict]:
    items = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    if subset == "fast":
        items = [item for item in items if "fast" in item["tags"]]
    if limit:
        items = items[:limit]
    return items


async def run_pipeline(items: list[dict]) -> list[ItemResult]:
    import tempfile

    from app.agent.runner import build_dependencies, run_turn
    from app.core.db import async_session_factory, transactional_sessionmaker
    from app.models import Conversation, ConversationChannel, Organization
    from app.services.embeddings import EmbeddingService
    from app.services.ingestion.seed import seed_corpus
    from app.services.storage import LocalFileStorage

    storage = LocalFileStorage(Path(tempfile.mkdtemp(prefix="helpdeck-eval-")))
    org_name = f"eval-{uuid.uuid4().hex[:8]}"
    print(f"Seeding ephemeral org {org_name!r} with {len(list(CORPUS.glob('*.md')))} docs…")
    summary = await seed_corpus(
        async_session_factory,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS,
        org_name=org_name,
    )
    deps = build_dependencies(sessionmaker=transactional_sessionmaker(async_session_factory))

    results: list[ItemResult] = []
    try:
        for index, item in enumerate(items, start=1):
            result = ItemResult(
                id=item["id"],
                question=item["question"],
                answerable=item["answerable"],
                expected_docs=item["expected_doc_ids"],
                ground_truth=item["ground_truth"],
            )
            started = time.perf_counter()
            try:
                async with async_session_factory() as session:
                    conversation = Conversation(
                        org_id=summary.org_id, channel=ConversationChannel.playground
                    )
                    session.add(conversation)
                    await session.commit()
                    conversation_id = conversation.id
                state = await run_turn(
                    deps,
                    org_id=summary.org_id,
                    conversation_id=conversation_id,
                    question=item["question"],
                )
                chunks = state.get("chunks") or []
                result.answer = state.get("response", "")
                result.escalated = bool(state.get("escalated", False))
                result.retrieved_docs = [c["document_title"] for c in chunks]
                result.contexts = [c["content"] for c in chunks]
                result.citations = len(state.get("citations") or [])
            except Exception as exc:  # noqa: BLE001 - record and continue
                result.error = str(exc)
            results.append(result)
            elapsed = time.perf_counter() - started
            marker = "ESC" if result.escalated else f"{result.citations}c"
            print(f"  [{index}/{len(items)}] {item['id']} {marker} {elapsed:.1f}s", flush=True)
    finally:
        async with async_session_factory() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()
    return results


def deterministic_metrics(results: list[ItemResult]) -> dict:
    answerable = [r for r in results if r.answerable and r.error is None]
    unanswerable = [r for r in results if not r.answerable and r.error is None]

    recalls, precisions = [], []
    for result in answerable:
        expected = set(result.expected_docs)
        retrieved = set(result.retrieved_docs)
        recalls.append(len(expected & retrieved) / len(expected) if expected else 0.0)
        if result.retrieved_docs:
            hits = sum(1 for doc in result.retrieved_docs if doc in expected)
            precisions.append(hits / len(result.retrieved_docs))

    answered = [r for r in answerable if not r.escalated]
    metrics = {
        "context_recall": statistics.mean(recalls) if recalls else 0.0,
        "context_precision": statistics.mean(precisions) if precisions else 0.0,
        "refusal_accuracy": (
            statistics.mean(1.0 if r.escalated else 0.0 for r in unanswerable)
            if unanswerable
            else None
        ),
        "citation_validity": (
            statistics.mean(1.0 if r.citations > 0 else 0.0 for r in answered) if answered else 0.0
        ),
        "answered_rate": (len(answered) / len(answerable)) if answerable else 0.0,
        "errors": sum(1 for r in results if r.error is not None),
    }
    return metrics


def ragas_metrics(results: list[ItemResult], judge_model: str) -> dict:
    """Judged metrics on answered items via ragas + a local Ollama judge."""
    _shim_ragas_imports()
    from langchain_ollama import ChatOllama, OllamaEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.dataset_schema import SingleTurnSample
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import AnswerRelevancy, Faithfulness

    from app.core.config import get_settings

    settings = get_settings()
    answered = [
        r for r in results if r.answerable and not r.escalated and r.error is None and r.contexts
    ]
    if not answered:
        return {"faithfulness": None, "answer_relevancy": None, "judged_items": 0}

    judge = LangchainLLMWrapper(
        ChatOllama(model=judge_model, base_url=settings.ollama_base_url, temperature=0.0)
    )
    embedder = LangchainEmbeddingsWrapper(
        OllamaEmbeddings(
            model=settings.embedding_model.split("/")[-1], base_url=settings.ollama_base_url
        )
    )
    dataset = EvaluationDataset(
        samples=[
            SingleTurnSample(
                user_input=r.question,
                response=r.answer,
                retrieved_contexts=r.contexts,
                reference=r.ground_truth,
            )
            for r in answered
        ]
    )
    outcome = evaluate(
        dataset,
        metrics=[Faithfulness(llm=judge), AnswerRelevancy(llm=judge, embeddings=embedder)],
        llm=judge,
        embeddings=embedder,
        show_progress=True,
    )
    frame = outcome.to_pandas()

    def clean_mean(column: str) -> float | None:
        # Small local judges sometimes fail ragas' structured-output parsing,
        # yielding NaN for that sample; average what parsed, None if nothing.
        series = frame[column].dropna()
        return float(series.mean()) if len(series) else None

    faithfulness = clean_mean("faithfulness")
    relevancy = clean_mean("answer_relevancy")
    return {
        "faithfulness": faithfulness,
        "answer_relevancy": relevancy,
        "judged_items": len(answered),
        "judge_parse_failures": int(frame["faithfulness"].isna().sum()),
    }


def print_summary(metrics: dict, thresholds: dict, passed: bool | None) -> None:
    print("\n== Eval summary " + "=" * 44)
    for key, value in metrics.items():
        gate = ""
        if key in thresholds and isinstance(value, float):
            gate = (
                f"  (gate >= {thresholds[key]:.2f}: {'OK' if value >= thresholds[key] else 'FAIL'})"
            )
        shown = f"{value:.3f}" if isinstance(value, float) else str(value)
        print(f"  {key:20s} {shown}{gate}")
    if passed is not None:
        print(f"  {'GATE':20s} {'PASSED' if passed else 'FAILED'}")
    print("=" * 60)


async def persist_run(
    kind: str,
    dataset: str,
    metrics: dict,
    thresholds: dict,
    passed: bool | None,
    duration: float,
    item_count: int,
    report: dict,
) -> None:
    from app.core.config import get_settings
    from app.core.db import async_session_factory
    from app.models import EvalRun

    settings = get_settings()
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        sha = stdout.decode().strip()[:40]
    except OSError:
        sha = None
    async with async_session_factory() as session:
        session.add(
            EvalRun(
                kind=kind,
                git_sha=sha or None,
                dataset=dataset,
                item_count=item_count,
                model_config={
                    "cheap": settings.llm_cheap_model,
                    "strong": settings.llm_strong_model,
                    "embedding": settings.embedding_model,
                },
                metrics={k: v for k, v in metrics.items() if v is not None},
                thresholds=thresholds,
                passed=passed,
                duration_s=duration,
                report=report,
            )
        )
        await session.commit()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", choices=["fast", "full"], default="fast")
    parser.add_argument("--limit", type=int, default=None, help="cap item count (debugging)")
    parser.add_argument("--ragas", dest="ragas", action="store_true", default=False)
    parser.add_argument("--no-ragas", dest="ragas", action="store_false")
    parser.add_argument("--gate", action="store_true", help="exit non-zero on threshold failure")
    parser.add_argument("--judge-model", default=None, help="Ollama judge (default: cheap model)")
    parser.add_argument(
        "--min-faithfulness",
        type=float,
        default=None,
        help="also gate judged faithfulness (only with --ragas)",
    )
    parser.add_argument("--kind", default=None, help="eval_runs.kind (default: local)")
    args = parser.parse_args()

    from app.core.config import get_settings

    items = load_items(args.subset, args.limit)
    print(f"Running {len(items)} golden items (subset={args.subset}, ragas={args.ragas})")
    started = time.perf_counter()
    results = await run_pipeline(items)

    metrics = deterministic_metrics(results)
    if args.ragas:
        judge = args.judge_model or get_settings().llm_cheap_model.split("/")[-1]
        print(f"Scoring RAGAS metrics with judge {judge!r}…")
        metrics.update(ragas_metrics(results, judge))

    thresholds = dict(THRESHOLDS)
    if args.ragas and args.min_faithfulness is not None:
        thresholds["faithfulness"] = args.min_faithfulness

    passed: bool | None = None
    if args.gate:
        passed = all(
            metrics.get(key) is not None and metrics[key] >= minimum
            for key, minimum in thresholds.items()
        )

    duration = time.perf_counter() - started
    report = {
        "items": [
            {
                "id": r.id,
                "escalated": r.escalated,
                "citations": r.citations,
                "retrieved_docs": r.retrieved_docs,
                "error": r.error,
            }
            for r in results
        ]
    }
    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = REPORTS / f"{stamp}-{args.subset}.json"
    out_path.write_text(
        json.dumps(
            {
                "created_at": stamp,
                "subset": args.subset,
                "metrics": metrics,
                "thresholds": thresholds,
                "passed": passed,
                "duration_s": duration,
                **report,
            },
            indent=2,
        )
    )
    print(f"Report written to {out_path.relative_to(REPO_ROOT)}")

    await persist_run(
        args.kind or "local",
        f"golden:{args.subset}",
        metrics,
        thresholds,
        passed,
        duration,
        len(items),
        report,
    )
    print_summary(metrics, thresholds, passed)
    return 0 if passed in (None, True) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
