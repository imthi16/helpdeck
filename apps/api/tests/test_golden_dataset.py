"""Golden dataset loader/schema checks (task 6.2).

Every item was hand-written and hand-reviewed; this test keeps the file
honest as it grows: schema, unique ids, doc slugs that actually exist in the
corpus, a 10–20% unanswerable share, and a fast subset that covers every doc
and includes enough refusal cases for the CI gate.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "eval" / "golden.jsonl"
CORPUS = REPO_ROOT / "eval" / "fixtures" / "corpus"

REQUIRED_KEYS = {"id", "question", "ground_truth", "expected_doc_ids", "answerable", "tags"}


def load_items() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def test_schema_and_ids() -> None:
    items = load_items()
    assert 100 <= len(items) <= 200
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids), "duplicate ids"
    corpus_slugs = {path.stem for path in CORPUS.glob("*.md")}
    for item in items:
        assert set(item.keys()) == REQUIRED_KEYS, item["id"]
        assert item["question"].strip() and item["ground_truth"].strip(), item["id"]
        assert isinstance(item["answerable"], bool), item["id"]
        assert isinstance(item["tags"], list), item["id"]
        if item["answerable"]:
            assert item["expected_doc_ids"], f"{item['id']}: answerable items need doc ids"
            for slug in item["expected_doc_ids"]:
                assert slug in corpus_slugs, f"{item['id']}: unknown corpus doc {slug!r}"
        else:
            assert item["expected_doc_ids"] == [], f"{item['id']}: refusals have no doc ids"


def test_unanswerable_share() -> None:
    items = load_items()
    refusals = sum(not item["answerable"] for item in items)
    assert 0.10 <= refusals / len(items) <= 0.20


def test_fast_subset_covers_corpus_with_refusals() -> None:
    items = load_items()
    fast = [item for item in items if "fast" in item["tags"]]
    assert len(fast) == 30
    assert sum(not item["answerable"] for item in fast) >= 5
    covered = {slug for item in fast for slug in item["expected_doc_ids"]}
    corpus_slugs = {path.stem for path in CORPUS.glob("*.md")}
    assert covered == corpus_slugs, f"fast subset missing docs: {corpus_slugs - covered}"
