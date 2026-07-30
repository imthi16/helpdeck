"""Evaluation run history — one row per eval run (plans 6.3 and 6.5).

Two kinds share the table because they answer the same question — how good are the
answers — and 6.6 charts them the same way:

- ``golden`` (6.3): an offline run over ``eval/golden.jsonl``. Scores the pipeline
  against a fixture corpus, belongs to no tenant, ``org_id`` is NULL, written by
  the eval CLI as the superuser.
- ``online`` (6.5): a nightly sample of one org's real conversations, re-scored for
  faithfulness. This *is* tenant data, so it carries ``org_id`` and is written by
  the worker as the app role.

RLS keeps them apart. The tenant policy is the usual ``org_id = current_tenant``,
under which golden rows (NULL org) match no tenant — deliberate, since a tenant's
Quality card should show quality measured on their own traffic, not on a demo
corpus they have never seen.
"""

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class EvalRunKind(enum.StrEnum):
    """Stored as text so old rows survive new values."""

    #: Offline run over eval/golden.jsonl (6.3).
    golden = "golden"
    #: Sampled production conversations judged online (6.5).
    online = "online"


class EvalRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_created_at", "created_at"),
        Index("ix_eval_runs_org_id_created_at", "org_id", text("created_at DESC")),
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=EvalRunKind.golden)
    #: NULL for golden runs — they score a fixture corpus and belong to no tenant.
    #: Set for online runs (6.5), which aggregate one org's real conversations and
    #: are therefore that org's data. RLS keeps the two apart: the tenant policy is
    #: ``org_id = current_tenant``, so golden rows are invisible to the app role.
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    #: Dataset filename, e.g. "golden.jsonl".
    dataset: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Which slice ran: "full", or a tag such as "fast" (the 6.4 CI gate).
    subset: Mapped[str] = mapped_column(String(32), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Aggregate scores (faithfulness, context_recall, refusal_accuracy, ...).
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Which models produced those scores. Without this a row is unreadable: an
    #: offline-stub run and a real-model run are not comparable numbers.
    environment: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Gate verdict; NULL when no thresholds were applied (6.4 sets it).
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    git_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Where the full per-item report was written (reports are not committed).
    report_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Only created_at: an eval run is a point-in-time fact and is never updated.
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
