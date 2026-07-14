"""re-dimension chunk embeddings for oss model (768)

Revision ID: 8bb42227bfe1
Revises: 51fe866934da
Create Date: 2026-07-14 16:21:46.821481

Open-source embedding models are not 1536-dim like ``text-embedding-3-small``.
The default OSS model (``nomic-embed-text`` via Ollama) is 768-dim, so the
``chunks.embedding`` column and its HNSW index are re-created at the new width.

Existing embeddings are cleared (they were produced by a different model and are
not comparable) — re-run ingestion to repopulate. The column stays nullable, so
NULLing is safe; only the vector width changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8bb42227bfe1"
down_revision: str | Sequence[str] | None = "51fe866934da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_DIMS = 1536
NEW_DIMS = 768


def _redimension(dims: int) -> None:
    # Clear vectors (different model — not comparable), drop the dimension-bound
    # HNSW index, change the width, then rebuild the index.
    op.execute("UPDATE chunks SET embedding = NULL")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute(f"ALTER TABLE chunks ALTER COLUMN embedding TYPE vector({dims})")
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    _redimension(NEW_DIMS)


def downgrade() -> None:
    _redimension(OLD_DIMS)
