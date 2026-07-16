# ADR-001: pgvector over a dedicated vector database

**Status:** accepted (Phase 1, 2026)

## Context

Retrieval needs dense vector search over tenant document chunks. Options:
a dedicated vector DB (Pinecone, Qdrant, Weaviate, Milvus) or pgvector
inside the Postgres instance we already run for everything else.

## Decision

Use **pgvector (HNSW, cosine)** in the primary Postgres, alongside a
generated `tsvector` column for full-text search, fused with Reciprocal
Rank Fusion in the application.

## Rationale

- **One database, one transaction boundary.** Chunks, their embeddings, and
  tenant metadata live in the same row — ingest is a single upsert, deletes
  cascade, and there is no dual-write consistency problem between a vector
  store and the system of record.
- **RLS applies to vectors too.** Tenant isolation (ADR-003) is enforced by
  the same FORCE RLS policies on `chunks`; a separate vector DB would need
  its own bespoke isolation story.
- **Hybrid search is a JOIN away.** BM25-style `ts_rank_cd` and dense
  similarity run in parallel queries against the same table.
- **Operational cost.** Neon serves pgvector on the free tier; a dedicated
  vector DB is another service, another bill, another failure mode — at our
  scale (thousands of chunks per tenant) HNSW in Postgres is far from its
  limits.

## Consequences

- Very large corpora (tens of millions of vectors) would eventually argue
  for a dedicated engine; that is a re-evaluation point, not a migration we
  pre-build.
- Embedding dimension is baked into the column type — switching embedding
  models requires a re-dimension migration and re-embedding (documented in
  `docs/deploy.md`).
