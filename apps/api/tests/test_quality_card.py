"""Quality card data (task 6.6).

The card reports on a tenant's own answers, so the failure modes that matter are
showing them someone else's numbers, showing a golden-set score as if it were
theirs, or rendering "never measured" as 0% — which reads as total failure rather
than as an absent measurement.
"""

import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import EvalRun, EvalRunKind, Organization
from app.services.quality import quality_summary

Sessionmaker = async_sessionmaker[AsyncSession]


async def _org(session: AsyncSession, name: str) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=name)
    session.add(org)
    await session.flush()
    return org.id


def _run(
    org_id: uuid.UUID | None,
    faithfulness: float | None,
    *,
    kind: EvalRunKind = EvalRunKind.online,
    created_at: dt.datetime,
    items: int = 10,
    alerted: bool = False,
) -> EvalRun:
    return EvalRun(
        id=uuid.uuid4(),
        kind=kind,
        org_id=org_id,
        dataset="production",
        subset="7d-sample",
        item_count=items,
        metrics={"faithfulness": faithfulness},
        environment={"alerted": alerted},
        duration_ms=0,
        created_at=created_at,
    )


BASE = dt.datetime(2026, 7, 20, 3, 0, tzinfo=dt.UTC)


async def test_an_org_never_sampled_has_no_score_rather_than_zero(
    db_sessionmaker: Sessionmaker,
) -> None:
    """0% would read as "every answer is a hallucination"; the truth is "no data"."""
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Fresh Co")

        summary = await quality_summary(session, org_id)

    assert summary.latest_faithfulness is None
    assert summary.delta is None
    assert summary.trend == []
    assert summary.alerted is False


async def test_the_latest_run_and_its_baseline(db_sessionmaker: Sessionmaker) -> None:
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Trend Co")
        session.add_all(
            [
                _run(org_id, 0.90, created_at=BASE),
                _run(org_id, 0.94, created_at=BASE + dt.timedelta(days=1)),
                _run(org_id, 0.80, created_at=BASE + dt.timedelta(days=2)),
            ]
        )
        await session.flush()

        summary = await quality_summary(session, org_id)

    assert summary.latest_faithfulness == 0.80
    assert summary.baseline_faithfulness == pytest.approx(0.92)
    assert summary.delta == pytest.approx(-0.12)
    assert [p.faithfulness for p in summary.trend] == [0.90, 0.94, 0.80], "oldest first"


async def test_golden_runs_are_never_shown_as_a_tenants_quality(
    db_sessionmaker: Sessionmaker,
) -> None:
    """A golden run scores a fixture corpus this tenant has never seen, and is
    scored against full retrieval rather than cited chunks — charting the two
    together would invent a trend out of a methodology change."""
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Golden Co")
        session.add_all(
            [
                _run(None, 1.0, kind=EvalRunKind.golden, created_at=BASE),
                _run(org_id, 0.70, created_at=BASE + dt.timedelta(days=1)),
            ]
        )
        await session.flush()

        summary = await quality_summary(session, org_id)

    assert summary.latest_faithfulness == 0.70
    assert len(summary.trend) == 1


async def test_one_orgs_scores_never_appear_on_anothers_card(
    db_sessionmaker: Sessionmaker,
) -> None:
    async with db_sessionmaker() as session:
        mine = await _org(session, "Mine Co")
        theirs = await _org(session, "Theirs Co")
        session.add_all(
            [
                _run(mine, 0.60, created_at=BASE),
                _run(theirs, 0.99, created_at=BASE + dt.timedelta(days=1)),
            ]
        )
        await session.flush()

        summary = await quality_summary(session, mine)

    assert summary.latest_faithfulness == 0.60
    assert len(summary.trend) == 1


async def test_a_night_with_nothing_measured_is_omitted_not_plotted_as_zero(
    db_sessionmaker: Sessionmaker,
) -> None:
    """6.5 records a row with faithfulness None when every answer was a refusal."""
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Refusal Co")
        session.add_all(
            [
                _run(org_id, 0.90, created_at=BASE),
                _run(org_id, None, created_at=BASE + dt.timedelta(days=1)),
            ]
        )
        await session.flush()

        summary = await quality_summary(session, org_id)

    assert summary.latest_faithfulness == 0.90, "the unmeasured night is not the latest"
    assert len(summary.trend) == 1


async def test_the_regression_flag_follows_the_latest_run(
    db_sessionmaker: Sessionmaker,
) -> None:
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Alert Co")
        session.add_all(
            [
                _run(org_id, 0.95, created_at=BASE, alerted=True),
                _run(org_id, 0.94, created_at=BASE + dt.timedelta(days=1), alerted=False),
            ]
        )
        await session.flush()

        summary = await quality_summary(session, org_id)

    assert summary.alerted is False, "a stale alert must not stick to the card"


async def test_the_trend_is_capped(db_sessionmaker: Sessionmaker) -> None:
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Long Co")
        session.add_all(
            [_run(org_id, 0.9, created_at=BASE + dt.timedelta(days=i)) for i in range(30)]
        )
        await session.flush()

        summary = await quality_summary(session, org_id, points=5)

    assert len(summary.trend) == 5


async def test_the_sample_size_travels_with_the_score(
    db_sessionmaker: Sessionmaker,
) -> None:
    """A 1.00 from two answers is a much weaker claim than a 1.00 from eighty."""
    async with db_sessionmaker() as session:
        org_id = await _org(session, "Denominator Co")
        session.add(_run(org_id, 1.0, created_at=BASE, items=2))
        await session.flush()

        summary = await quality_summary(session, org_id)

    assert summary.latest_sample_size == 2
    assert summary.trend[0].sample_size == 2
