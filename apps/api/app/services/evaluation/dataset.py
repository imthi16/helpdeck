"""Golden dataset loader (plan 6.2's file, read by 6.3's runner).

One JSON object per line in ``eval/golden.jsonl``. ``expected_doc_ids`` holds
corpus file stems, not UUIDs, because ``seed_corpus`` titles each document
``path.stem`` and document ids change on every re-seed — see docs/NOTES.md.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

# app/services/evaluation/dataset.py -> apps/api/app/services/evaluation
REPO_ROOT = Path(__file__).resolve().parents[5]
GOLDEN_PATH = REPO_ROOT / "eval" / "golden.jsonl"

REQUIRED_KEYS = frozenset(
    {"id", "question", "ground_truth", "expected_doc_ids", "answerable", "tags"}
)


class GoldenDatasetError(Exception):
    pass


@dataclass(frozen=True)
class GoldenItem:
    id: str
    question: str
    #: The expected answer, or the expected refusal when ``answerable`` is False.
    ground_truth: str
    #: Corpus slugs that must be retrieved. Empty for unanswerable items.
    expected_doc_ids: tuple[str, ...]
    answerable: bool
    tags: tuple[str, ...]


def load_golden(path: Path | None = None) -> list[GoldenItem]:
    source = path or GOLDEN_PATH
    if not source.exists():
        raise GoldenDatasetError(f"golden dataset not found at {source}")
    items: list[GoldenItem] = []
    for lineno, line in enumerate(source.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenDatasetError(f"{source}:{lineno}: invalid JSON: {exc}") from exc
        missing = REQUIRED_KEYS - set(raw)
        if missing:
            raise GoldenDatasetError(f"{source}:{lineno}: missing keys {sorted(missing)}")
        items.append(
            GoldenItem(
                id=raw["id"],
                question=raw["question"],
                ground_truth=raw["ground_truth"],
                expected_doc_ids=tuple(raw["expected_doc_ids"]),
                answerable=bool(raw["answerable"]),
                tags=tuple(raw["tags"]),
            )
        )
    if not items:
        raise GoldenDatasetError(f"{source} contains no items")
    return items


def select_subset(
    items: Iterable[GoldenItem], *, tag: str | None = None, limit: int | None = None
) -> list[GoldenItem]:
    """Filter by tag (e.g. ``fast`` for the 6.4 CI gate), then cap the count.

    The cap keeps dataset order rather than sampling, so two runs of
    ``--limit N`` score the same items and stay comparable.
    """
    selected: Sequence[GoldenItem] = [item for item in items if tag is None or tag in item.tags]
    if tag is not None and not selected:
        raise GoldenDatasetError(f"no golden items tagged {tag!r}")
    if limit is not None:
        selected = selected[:limit]
    return list(selected)
