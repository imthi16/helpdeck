"""Score the golden set against the live pipeline (plan 6.3).

Usage (from apps/api, so .env and the app package resolve):

    uv run python ../../eval/run_eval.py                  # full golden set
    uv run python ../../eval/run_eval.py --subset fast    # the 30-item CI subset

Requires a seeded demo org (`uv run python scripts/seed.py`) and the dev infra up.
Writes a JSON report to eval/reports/ and one row into eval_runs.

Each item is also appended to a .jsonl sink beside the report as it finishes, so a
run that is killed part-way can be continued instead of restarted:

    uv run python ../../eval/run_eval.py --subset fast --gate --no-db \
        --out ../../eval/reports/verify.json --resume ../../eval/reports/verify.jsonl

Pass the same base name and it appends in place, idempotently — the last line for an
id wins. Items that errored or timed out are re-run; only clean ones are reused. The
sink is deleted once a complete report is written.

Do not pipe this through `tail`: it emits per-call progress lines and a pipe buffers
them, so a healthy long run looks like a hang. Stream it, or `tee` it.
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import tracing  # noqa: E402
from app.services.evaluation.dataset import load_golden, select_subset  # noqa: E402
from app.services.evaluation.gate import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    evaluate_gate,
    format_gate,
)
from app.services.evaluation.metrics import JUDGE_ROUTE  # noqa: E402
from app.services.evaluation.runner import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    EvalSetupError,
    format_summary,
    run_eval,
    stub_providers,
)
from app.services.llm import LLMRoute  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subset",
        default="full",
        help="'full', or a golden.jsonl tag such as 'fast' (default: full)",
    )
    parser.add_argument("--limit", type=int, default=None, help="score only the first N items")
    parser.add_argument("--org-id", default=None, help="org to score against (default: demo org)")
    parser.add_argument("--out", default=None, help="write the JSON report here")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"items in flight (default: {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--item-timeout",
        type=float,
        default=None,
        help="seconds one item may take before it is recorded as an error "
        "(default: 3 x LLM_TIMEOUT_SECONDS)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="a previous run's .jsonl sink; items already scored there are reused, "
        "errored and timed-out ones are re-run",
    )
    parser.add_argument(
        "--no-db", action="store_true", help="skip the eval_runs row (report only)"
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="apply the 6.4 thresholds and exit non-zero if any blocking one fails",
    )
    parser.add_argument(
        "--judge-route",
        choices=[route.value for route in LLMRoute],
        default=JUDGE_ROUTE.value,
        help=(
            f"model that scores faithfulness/relevancy (default: {JUDGE_ROUTE.value}). "
            "'cheap' marks plainly supported claims unsupported and is not even reliably "
            "faster — see app/services/evaluation/metrics.py."
        ),
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    items = select_subset(
        load_golden(),
        tag=None if args.subset == "full" else args.subset,
        limit=args.limit,
    )

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        report = await run_eval(
            items=items,
            sessionmaker=sessionmaker,
            org_id=uuid.UUID(args.org_id) if args.org_id else None,
            subset=args.subset,
            concurrency=args.concurrency,
            judge_route=LLMRoute(args.judge_route),
            thresholds=DEFAULT_THRESHOLDS if args.gate else None,
            persist=not args.no_db,
            report_path=Path(args.out) if args.out else None,
            item_timeout=args.item_timeout,
            resume_path=Path(args.resume) if args.resume else None,
        )
    except EvalSetupError as exc:
        print(f"eval setup failed: {exc}", file=sys.stderr)
        return 2
    finally:
        # Short-lived process: push buffered spans before the loop closes.
        tracing.flush()
        await engine.dispose()

    print(format_summary(report))
    if report.counts["errors"]:
        # Errored items are excluded from the averages, so a run that mostly crashed
        # can still show healthy scores. Fail before the gate gets to bless it.
        return 1
    if args.gate:
        # A gate that can go green on stub scores is worse than no gate: both stubs
        # are lexical and flatter the pipeline. Refuse before applying thresholds.
        if stubs := stub_providers(report.environment):
            print(
                f"\n  gate FAILED — scored with stubs, not models ({', '.join(stubs)}).",
                file=sys.stderr,
            )
            return 1
        result = evaluate_gate(report.metrics)
        print(format_gate(result))
        return 0 if result.passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
