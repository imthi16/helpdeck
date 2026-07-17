"""Online sampling job (task 6.5): scoring, eval_runs row, regression alert."""

import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    Chunk,
    Conversation,
    ConversationChannel,
    Document,
    DocumentSourceType,
    DocumentStatus,
    EvalRun,
    Message,
    MessageRole,
    Organization,
)
from app.services.llm import LLMGateway, LLMUsage, OfflineGroundedProvider
from app.workers import online_eval
from app.workers.online_eval import sample_online_quality

Sessionmaker = async_sessionmaker[AsyncSession]


class FixedJudgeProvider:
    """Non-offline provider whose judge always answers a fixed score."""

    def __init__(self, score: float = 0.8) -> None:
        self.score = score
        self.calls = 0

    async def complete(self, model, messages, **kwargs) -> tuple[str, LLMUsage]:
        self.calls += 1
        return str(self.score), LLMUsage(prompt_tokens=1, completion_tokens=1)

    async def stream(self, model, messages, **kwargs) -> AsyncIterator[str]:
        yield str(self.score)


@pytest.fixture(autouse=True)
def eval_runs_cleanup(db_sessionmaker: Sessionmaker):
    yield


@pytest.fixture
async def cited_traffic(db_sessionmaker: Sessionmaker, monkeypatch: pytest.MonkeyPatch):
    """An org with one cited assistant answer from the last 24h."""
    # The job uses the module-global superuser factory; point it at the test's
    # loop-local one so sessions run on the right event loop.
    monkeypatch.setattr(online_eval, "async_session_factory", db_sessionmaker)

    marker = uuid.uuid4().hex[:8]
    async with db_sessionmaker() as session:
        org = Organization(name=f"online-eval-{marker}")
        session.add(org)
        await session.flush()
        document = Document(
            org_id=org.id,
            title="doc",
            source_type=DocumentSourceType.text,
            status=DocumentStatus.ready,
        )
        session.add(document)
        await session.flush()
        chunk = Chunk(
            org_id=org.id,
            document_id=document.id,
            content="Returns are accepted within 30 days.",
            meta={},
            token_count=7,
        )
        session.add(chunk)
        await session.flush()
        conversation = Conversation(org_id=org.id, channel=ConversationChannel.widget)
        session.add(conversation)
        await session.flush()
        message = Message(
            org_id=org.id,
            conversation_id=conversation.id,
            role=MessageRole.assistant,
            content="You can return items within 30 days. [1]",
            citations=[{"n": 1, "chunk_id": str(chunk.id)}],
            trace_id="cd" * 16,
        )
        session.add(message)
        await session.commit()
        org_id = org.id
    try:
        yield org_id
    finally:
        async with db_sessionmaker() as session:
            db_org = await session.get(Organization, org_id)
            if db_org is not None:
                await session.delete(db_org)
            for run in (
                (await session.execute(select(EvalRun).where(EvalRun.kind == "online")))
                .scalars()
                .all()
            ):
                await session.delete(run)
            await session.commit()


async def test_job_scores_and_records_eval_run(
    db_sessionmaker: Sessionmaker, cited_traffic, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[dict] = []
    monkeypatch.setattr(online_eval, "record_score", lambda **kwargs: recorded.append(kwargs))
    provider = FixedJudgeProvider(score=0.8)
    ctx = {"gateway": LLMGateway(provider, cheap_model="judge", strong_model="s")}

    result = await sample_online_quality(ctx)

    assert result["scored"] >= 1
    assert result["faithfulness_mean"] == pytest.approx(0.8)
    assert provider.calls >= 1
    assert any(s["name"] == "online_faithfulness" and s["trace_id"] == "cd" * 16 for s in recorded)

    async with db_sessionmaker() as session:
        run = (
            (
                await session.execute(
                    select(EvalRun)
                    .where(EvalRun.kind == "online")
                    .order_by(EvalRun.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert run is not None
        assert run.metrics["faithfulness_mean"] == pytest.approx(0.8)
        assert run.dataset == "production-sample"


async def test_job_skips_entirely_on_offline_provider(
    db_sessionmaker: Sessionmaker, cited_traffic
) -> None:
    ctx = {"gateway": LLMGateway(OfflineGroundedProvider(), cheap_model="c", strong_model="s")}
    result = await sample_online_quality(ctx)
    assert result == {"skipped": "offline"}


async def test_alert_fires_on_seven_day_regression(
    db_sessionmaker: Sessionmaker, cited_traffic, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    async with db_sessionmaker() as session:
        session.add(
            EvalRun(
                kind="online",
                dataset="production-sample",
                item_count=10,
                metrics={"faithfulness_mean": 0.9},
                created_at=now - datetime.timedelta(days=10),
            )
        )
        await session.commit()

    alerts: list[dict] = []

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **kwargs) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            alerts.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr(online_eval.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(online_eval, "record_score", lambda **kwargs: None)
    monkeypatch.setattr(
        online_eval.get_settings(), "alert_webhook_url", "https://hooks.example/x", raising=False
    )

    # Current run scores 0.5 -> drop of 0.4 vs the prior week's 0.9.
    ctx = {"gateway": LLMGateway(FixedJudgeProvider(score=0.5), cheap_model="j", strong_model="s")}
    result = await sample_online_quality(ctx)
    assert result["faithfulness_mean"] == pytest.approx(0.5)
    assert alerts and "regression" in alerts[0]["json"]["text"]
