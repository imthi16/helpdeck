"""Seed a demo org from the eval corpus.

Usage (from apps/api):  uv run python scripts/seed.py
"""

import asyncio
import sys
from pathlib import Path

# Allow `python scripts/seed.py` from apps/api by putting the package root on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.embeddings import EmbeddingService  # noqa: E402
from app.services.ingestion.seed import seed_corpus  # noqa: E402
from app.services.storage import get_storage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        summary = await seed_corpus(
            sessionmaker,
            EmbeddingService(),
            get_storage(),
            corpus_dir=CORPUS_DIR,
        )
    finally:
        await engine.dispose()

    print("Seeded demo org")
    print(f"  org_id:    {summary.org_id}")
    print(f"  documents: {summary.document_count}")
    print(f"  chunks:    {summary.chunk_count}")


if __name__ == "__main__":
    asyncio.run(main())
