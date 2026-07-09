from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def fetch_indexdefs(engine: AsyncEngine, table: str) -> dict[str, str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"),
            {"t": table},
        )
        return {name: definition for name, definition in rows}


async def test_chunks_hnsw_index_present(db_engine: AsyncEngine) -> None:
    indexes = await fetch_indexdefs(db_engine, "chunks")
    hnsw = indexes.get("ix_chunks_embedding_hnsw", "")
    assert "USING hnsw" in hnsw
    assert "vector_cosine_ops" in hnsw


async def test_chunks_gin_tsv_index_present(db_engine: AsyncEngine) -> None:
    indexes = await fetch_indexdefs(db_engine, "chunks")
    assert "USING gin" in indexes.get("ix_chunks_content_tsv", "")


async def test_chunks_org_document_composite_index_present(db_engine: AsyncEngine) -> None:
    indexes = await fetch_indexdefs(db_engine, "chunks")
    assert "(org_id, document_id)" in indexes.get("ix_chunks_org_document", "")


async def test_expected_tables_exist(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {row[0] for row in rows}
    assert {"organizations", "users", "memberships", "documents", "chunks"} <= tables


async def test_content_tsv_is_generated(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT is_generated FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'content_tsv'"
            )
        )
        assert row.scalar() == "ALWAYS"
