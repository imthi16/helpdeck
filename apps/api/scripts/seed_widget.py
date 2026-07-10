"""Seed a fixed-key widget demo org for the widget E2E and local demos.

Usage (from apps/api):  uv run python -m scripts.seed_widget
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services.embeddings import EmbeddingService  # noqa: E402
from app.services.ingestion.seed import seed_corpus  # noqa: E402
from app.services.storage import get_storage  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "eval" / "fixtures" / "corpus"

WIDGET_DEMO_ORG = "Widget Demo Co"
WIDGET_DEMO_KEY = "pk_widget_demo"


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
            org_name=WIDGET_DEMO_ORG,
            public_key=WIDGET_DEMO_KEY,
        )
    finally:
        await engine.dispose()

    print(f"Seeded widget demo org {summary.org_id} (key {WIDGET_DEMO_KEY})")
    print(f"  documents: {summary.document_count}  chunks: {summary.chunk_count}")


if __name__ == "__main__":
    asyncio.run(main())
