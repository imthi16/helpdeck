"""Score the golden set against the live pipeline (plan 6.3).

Usage (from apps/api, so .env and the app package resolve):

    uv run python ../../eval/run_eval.py                  # full golden set
    uv run python ../../eval/run_eval.py --subset fast    # the 30-item CI subset

Requires a seeded demo org (`uv run python scripts/seed.py`) and the dev infra up.
Writes a JSON report to eval/reports/ and one row into eval_runs.
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
from app.services.evaluation.metrics import JUDGE_ROUTE  # noqa: E402
from app.services.evaluation.runner import (  # noqa: E402
    DEFAULT_CONCURRENCY,
    EvalSetupError,
    format_summary,
    run_eval,
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
        "--no-db", action="store_true", help="skip the eval_runs row (report only)"
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
            persist=not args.no_db,
            report_path=Path(args.out) if args.out else None,
        )
    except EvalSetupError as exc:
        print(f"eval setup failed: {exc}", file=sys.stderr)
        return 2
    finally:
        # Short-lived process: push buffered spans before the loop closes.
        tracing.flush()
        await engine.dispose()

    print(format_summary(report))
    return 1 if report.counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
