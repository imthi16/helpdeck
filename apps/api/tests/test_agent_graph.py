import uuid
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.graph import build_agent_graph, parse_citations, parse_confidence, parse_intent
from app.agent.runner import build_dependencies, run_turn
from app.core.config import get_settings
from app.core.db import transactional_sessionmaker
from app.models import (
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Escalation,
    Organization,
)
from app.services.embeddings import EmbeddingService
from app.services.ingestion.seed import seed_corpus
from app.services.storage import LocalFileStorage

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"
Sessionmaker = async_sessionmaker[AsyncSession]


# --- pure parsing helpers ----------------------------------------------------


def test_parse_intent() -> None:
    assert parse_intent("faq") == "faq"
    assert parse_intent("The label is human_request.") == "human_request"
    assert parse_intent("chitchat\n") == "chitchat"
    assert parse_intent("something else") == "faq"  # default


def test_parse_citations_maps_and_validates() -> None:
    chunks = [
        {"chunk_id": "c1", "document_id": "d1", "document_title": "t1", "content": "aaa"},
        {"chunk_id": "c2", "document_id": "d2", "document_title": "t2", "content": "bbb"},
    ]
    citations = parse_citations("Fact one [1] and fact two [2]. Also [1] again.", chunks)
    assert [c["n"] for c in citations] == [1, 2]  # dedup, in order
    assert citations[0]["chunk_id"] == "c1"

    # Out-of-range citation is dropped (guards fabricated [n]).
    assert parse_citations("See [9]", chunks) == []


def test_parse_confidence() -> None:
    assert parse_confidence("0.95") == 0.95
    assert parse_confidence("score: 0.4 overall") == 0.4
    assert parse_confidence("1") == 1.0
    assert parse_confidence("2.5") == 1.0  # clamped
    assert parse_confidence("no number") == 0.0


# --- integration on the seeded corpus ---------------------------------------


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker, tmp_path: Path):
    storage = LocalFileStorage(tmp_path)
    summary = await seed_corpus(
        db_sessionmaker,
        EmbeddingService(),
        storage,
        corpus_dir=CORPUS_DIR,
        org_name=f"agent-test-{uuid.uuid4()}",
    )
    try:
        yield summary.org_id
    finally:
        async with db_sessionmaker() as session:
            org = await session.get(Organization, summary.org_id)
            if org is not None:
                await session.delete(org)
                await session.commit()


async def _make_conversation(sm: Sessionmaker, org_id: uuid.UUID) -> uuid.UUID:
    async with sm() as session:
        conversation = Conversation(org_id=org_id, channel=ConversationChannel.playground)
        session.add(conversation)
        await session.commit()
        return conversation.id


async def _escalations(sm: Sessionmaker, conversation_id: uuid.UUID) -> list[Escalation]:
    async with sm() as session:
        return list(
            (
                await session.execute(
                    select(Escalation).where(Escalation.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )


async def test_in_kb_question_answers_with_valid_citations(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    conversation_id = await _make_conversation(db_sessionmaker, org_id)
    deps = build_dependencies(sessionmaker=transactional_sessionmaker(db_sessionmaker))

    result = await run_turn(
        deps,
        org_id=org_id,
        conversation_id=conversation_id,
        question="How often should I descale my espresso machine?",
        checkpointer=InMemorySaver(),
    )

    assert result["intent"] == "faq"
    assert not result.get("escalated")
    assert "[" in result["answer"] and "]" in result["answer"]
    assert result["citations"], "expected at least one citation"

    # Citations map to real chunks in this org.
    async with db_sessionmaker() as session:
        from app.models import Chunk

        for citation in result["citations"]:
            chunk = await session.get(Chunk, uuid.UUID(citation["chunk_id"]))
            assert chunk is not None
            assert chunk.org_id == org_id

    assert await _escalations(db_sessionmaker, conversation_id) == []


async def test_out_of_kb_question_refuses_and_escalates(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    conversation_id = await _make_conversation(db_sessionmaker, org_id)
    deps = build_dependencies(sessionmaker=transactional_sessionmaker(db_sessionmaker))

    result = await run_turn(
        deps,
        org_id=org_id,
        conversation_id=conversation_id,
        question="What is your CEO's shoe size?",
        checkpointer=InMemorySaver(),
    )

    assert result["escalated"] is True
    assert result["citations"] == []

    escalations = await _escalations(db_sessionmaker, conversation_id)
    assert len(escalations) == 1
    async with db_sessionmaker() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.status == ConversationStatus.escalated


async def test_human_request_escalates_without_retrieval(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    conversation_id = await _make_conversation(db_sessionmaker, org_id)
    deps = build_dependencies(sessionmaker=transactional_sessionmaker(db_sessionmaker))

    result = await run_turn(
        deps,
        org_id=org_id,
        conversation_id=conversation_id,
        question="Can I please talk to a human agent?",
        checkpointer=InMemorySaver(),
    )

    assert result["intent"] == "human_request"
    assert result["escalated"] is True
    assert result.get("chunks") is None  # retrieval was skipped
    escalations = await _escalations(db_sessionmaker, conversation_id)
    assert len(escalations) == 1
    assert "human" in escalations[0].reason.lower()


def _psycopg_dsn() -> str:
    # AsyncPostgresSaver uses psycopg; strip SQLAlchemy's +asyncpg driver tag.
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


async def test_postgres_checkpointer_persists_turn_state(
    db_sessionmaker: Sessionmaker, seeded: uuid.UUID
) -> None:
    org_id = seeded
    conversation_id = await _make_conversation(db_sessionmaker, org_id)
    deps = build_dependencies(sessionmaker=transactional_sessionmaker(db_sessionmaker))

    async with AsyncPostgresSaver.from_conn_string(_psycopg_dsn()) as saver:
        await saver.setup()  # library-managed checkpoint tables (not app schema)
        graph = build_agent_graph(deps, checkpointer=saver)
        config = {"configurable": {"thread_id": str(conversation_id)}}
        result = await graph.ainvoke(
            {
                "org_id": str(org_id),
                "conversation_id": str(conversation_id),
                "question": "How often should I descale my espresso machine?",
            },
            config=config,
        )
        assert result["citations"]

        # State was checkpointed under the conversation's thread_id.
        snapshot = await graph.aget_state(config)
        assert snapshot.values["question"].startswith("How often")
        assert snapshot.values["citations"] == result["citations"]
