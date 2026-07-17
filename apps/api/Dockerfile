# HelpDeck API + worker image (task 7.1).
# One image, two commands: the default CMD serves the API; the worker service
# overrides it with `uv run arq app.workers.main.WorkerSettings`.
# Build context is the REPO ROOT (docker build -f apps/api/Dockerfile .) so the
# demo corpus ships with the image for seeding and the demo-org nightly reset.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini ./
COPY apps/api/scripts ./scripts
# Demo corpus at /app/eval/fixtures/corpus (found by find_corpus_dir()).
COPY eval/fixtures/corpus ./eval/fixtures/corpus

RUN useradd --create-home --uid 10001 helpdeck \
    && chown -R helpdeck:helpdeck /app
USER helpdeck

EXPOSE 8000

# $PORT is provided by Render/Railway; default for local docker run.
CMD ["sh", "-c", "uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
