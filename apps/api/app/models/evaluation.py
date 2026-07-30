"""Evaluation run history — one row per offline eval run (plan 6.3).

A system table, not tenant data: a golden-set run scores the pipeline against a
fixture corpus, so there is no ``org_id`` and no RLS, following the ``audit_logs``
precedent. The app role is granted SELECT only (6.6's Quality card reads the
latest row); runs are written by the CLI as the superuser. 6.5 adds org scoping
when it starts scoring real per-tenant traffic.
"""

import datetime as dt
import enum
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
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
    __table_args__ = (Index("ix_eval_runs_created_at", "created_at"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False, default=EvalRunKind.golden)
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
