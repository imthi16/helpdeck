"""Golden dataset loader + schema checks (task 6.2).

Every item was written by hand against `eval/fixtures/corpus` and reviewed
against the source text. This test keeps the file honest as it grows: parseable
JSONL, unique ids, doc slugs that really exist in the corpus, refusal items
carrying no doc ids, a 10-20% unanswerable share, and a `fast` subset (the 6.4
CI gate) that covers every document and contains enough refusals to measure
refusal accuracy.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "eval" / "golden.jsonl"
CORPUS = REPO_ROOT / "eval" / "fixtures" / "corpus"

REQUIRED_KEYS = {"id", "question", "ground_truth", "expected_doc_ids", "answerable", "tags"}
FAST_SUBSET_SIZE = 30


def load_golden() -> list[dict]:
    """The loader the eval runner uses: one JSON object per non-blank line."""
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def corpus_slugs() -> set[str]:
    # Slugs are file stems because `seed_corpus` titles each document `path.stem`.
    return {path.stem for path in CORPUS.glob("*.md")}


def test_dataset_size_and_unique_ids() -> None:
    items = load_golden()
    assert 100 <= len(items) <= 200, f"plan asks for 100-200 items, found {len(items)}"
    ids = [item["id"] for item in items]
    assert len(set(ids)) == len(ids), "duplicate ids"


def test_every_item_matches_the_schema() -> None:
    slugs = corpus_slugs()
    for item in load_golden():
        assert set(item) == REQUIRED_KEYS, f"{item.get('id')}: unexpected keys {set(item)}"
        assert item["question"].strip(), item["id"]
        assert item["ground_truth"].strip(), item["id"]
        assert isinstance(item["answerable"], bool), item["id"]
        assert isinstance(item["tags"], list), item["id"]
        if item["answerable"]:
            assert item["expected_doc_ids"], f"{item['id']}: answerable item needs doc ids"
            for slug in item["expected_doc_ids"]:
                assert slug in slugs, f"{item['id']}: unknown corpus doc {slug!r}"
        else:
            # A refusal has no supporting document by definition.
            assert item["expected_doc_ids"] == [], f"{item['id']}: refusal must have no doc ids"


def test_unanswerable_share_is_about_fifteen_percent() -> None:
    items = load_golden()
    refusals = sum(not item["answerable"] for item in items)
    share = refusals / len(items)
    assert 0.10 <= share <= 0.20, f"unanswerable share {share:.0%} outside 10-20%"


def test_questions_are_distinct() -> None:
    questions = [item["question"].strip().lower() for item in load_golden()]
    assert len(set(questions)) == len(questions), "duplicate questions"


def test_every_corpus_document_is_exercised() -> None:
    """A document nobody asks about is a retrieval blind spot the eval can't see."""
    covered = {slug for item in load_golden() for slug in item["expected_doc_ids"]}
    assert covered == corpus_slugs(), f"documents with no questions: {corpus_slugs() - covered}"


def test_fast_subset_is_a_usable_ci_gate() -> None:
    fast = [item for item in load_golden() if "fast" in item["tags"]]
    assert len(fast) == FAST_SUBSET_SIZE
    assert sum(not item["answerable"] for item in fast) >= 5, "too few refusals to gate on"
    covered = {slug for item in fast for slug in item["expected_doc_ids"]}
    assert covered == corpus_slugs(), f"fast subset misses documents: {corpus_slugs() - covered}"
