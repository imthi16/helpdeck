"""Quality metrics for the dashboard (plan 6.6).

Reads the ``eval_runs`` rows the nightly sampler writes (6.5) and shapes them for
the Analytics "Quality" card: the latest measurement plus a trend.

Only ``kind='online'`` rows are served. Golden runs measure the pipeline against a
fixture corpus, and RLS already hides them from a tenant — but the filter is
explicit here too, because "the org has no rows yet" and "the org is only allowed
to see some rows" should not be the same code path. The two kinds are also not
comparable: online faithfulness is scored against cited chunks only, so it is a
conservative lower bound (see ``services/evaluation/online.py``). Charting them on
one axis would invent a trend out of a methodology change.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvalRun, EvalRunKind

#: How many recent runs the sparkline shows. A fortnight is long enough to read a
#: trend and short enough that a fix last week is still visible in it.
TREND_POINTS = 14


@dataclass(frozen=True)
class QualityPoint:
    day: dt.date
    faithfulness: float
    #: Answers actually scored that night. Surfaced because a 1.0 from two answers
    #: and a 1.0 from eighty are very different claims, and a card that hides the
    #: denominator invites the reader to trust the small one.
    sample_size: int


@dataclass(frozen=True)
class QualitySummary:
    #: None when the org has never been sampled — a brand new org, or one whose
    #: answers were all refusals. Rendered as "not measured yet", never as zero.
    latest_faithfulness: float | None
    latest_sample_size: int
    latest_at: dt.datetime | None
    #: Mean of the preceding runs, for the "vs previous" delta. None with no history.
    baseline_faithfulness: float | None
    #: True when the nightly job flagged a regression on the latest run.
    alerted: bool
    trend: list[QualityPoint]

    @property
    def delta(self) -> float | None:
        if self.latest_faithfulness is None or self.baseline_faithfulness is None:
            return None
        return self.latest_faithfulness - self.baseline_faithfulness


def _faithfulness(run: EvalRun) -> float | None:
    value = (run.metrics or {}).get("faithfulness")
    return float(value) if isinstance(value, int | float) else None


async def quality_summary(
    session: AsyncSession, org_id: uuid.UUID, *, points: int = TREND_POINTS
) -> QualitySummary:
    """Latest online quality for one org, plus a short trend.

    Explicitly org-scoped as well as RLS-scoped, per the project rule.
    """
    result = await session.execute(
        select(EvalRun)
        .where(EvalRun.org_id == org_id, EvalRun.kind == EvalRunKind.online)
        .order_by(EvalRun.created_at.desc())
        .limit(points)
    )
    runs = list(result.scalars())
    # Runs with no faithfulness (a night where everything was a refusal) are
    # dropped rather than plotted as zero: nothing was measured, so there is no
    # point to draw, and a zero would read as a catastrophic regression.
    scored = [(run, value) for run in runs if (value := _faithfulness(run)) is not None]

    if not scored:
        return QualitySummary(
            latest_faithfulness=None,
            latest_sample_size=0,
            latest_at=None,
            baseline_faithfulness=None,
            alerted=False,
            trend=[],
        )

    latest_run, latest_value = scored[0]
    older = [value for _, value in scored[1:]]
    baseline = sum(older) / len(older) if older else None

    trend = [
        QualityPoint(
            day=run.created_at.date(),
            faithfulness=round(value, 4),
            sample_size=run.item_count,
        )
        # Oldest first: a chart reads left to right, and the query returns newest
        # first so the latest run is cheap to take.
        for run, value in reversed(scored)
    ]

    return QualitySummary(
        latest_faithfulness=round(latest_value, 4),
        latest_sample_size=latest_run.item_count,
        latest_at=latest_run.created_at,
        baseline_faithfulness=round(baseline, 4) if baseline is not None else None,
        alerted=bool((latest_run.environment or {}).get("alerted")),
        trend=trend,
    )
