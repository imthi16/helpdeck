from app.services.ingestion.chunker import chunk_text, count_tokens

WORDS = [
    "brewing",
    "grinder",
    "espresso",
    "portafilter",
    "temperature",
    "pressure",
    "extraction",
    "crema",
    "roast",
    "aroma",
]


def make_sentence(i: int) -> str:
    picks = " ".join(WORDS[(i + j) % len(WORDS)] for j in range(12))
    return f"Sentence {i} explains {picks} in detail."


def make_section(n_sentences: int, start: int = 0) -> str:
    return " ".join(make_sentence(i) for i in range(start, start + n_sentences))


def test_chunk_sizes_within_bounds() -> None:
    text = "# Guide\n\n" + make_section(200)
    chunks = chunk_text(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert count_tokens(chunk.content) <= 800
    for chunk in chunks[:-1]:
        assert chunk.token_count >= 500


def test_never_splits_mid_sentence() -> None:
    text = "# Guide\n\n" + make_section(120)
    for chunk in chunk_text(text):
        assert chunk.content.rstrip().endswith("in detail.")
        assert chunk.content.lstrip().startswith("Sentence")


def test_overlap_between_consecutive_chunks() -> None:
    text = "# Guide\n\n" + make_section(200)
    chunks = chunk_text(text)
    assert len(chunks) >= 2

    for previous, current in zip(chunks, chunks[1:], strict=False):
        # The next chunk must start with sentences repeated from the
        # previous chunk's tail (the overlap window).
        overlap = 0
        for sentence in current.content.split("in detail. "):
            candidate = sentence.strip()
            if candidate and candidate + " in detail." in previous.content + ".":
                overlap += count_tokens(candidate + " in detail.")
            else:
                break
        # 10–15% of 800-token chunks -> roughly 60–130 tokens of overlap.
        assert 40 <= overlap <= 160, f"overlap tokens out of range: {overlap}"


def test_heading_paths_preserved() -> None:
    text = (
        "# Manual\n\n"
        + make_section(30)
        + "\n\n## Setup\n\n"
        + make_section(30, start=100)
        + "\n\n### First use\n\n"
        + make_section(30, start=200)
        + "\n\n## Cleaning\n\n"
        + make_section(30, start=300)
    )
    chunks = chunk_text(text)
    paths = [tuple(chunk.metadata["heading_path"]) for chunk in chunks]

    assert ("Manual",) in paths
    assert ("Manual", "Setup") in paths
    assert ("Manual", "Setup", "First use") in paths
    assert ("Manual", "Cleaning") in paths


def test_chunks_do_not_cross_heading_boundaries() -> None:
    text = "# A\n\n" + make_section(40) + "\n\n# B\n\n" + make_section(40, start=500)
    chunks = chunk_text(text)

    for chunk in chunks:
        path = chunk.metadata["heading_path"]
        if path == ["A"]:
            assert "Sentence 5" not in chunk.content or "Sentence 50" not in chunk.content
            assert all(f"Sentence {i}" not in chunk.content for i in range(500, 540))
        if path == ["B"]:
            assert all(f"Sentence {i} " not in chunk.content for i in range(0, 40))


def test_base_metadata_and_index_carried() -> None:
    text = "# Guide\n\n" + make_section(120)
    chunks = chunk_text(text, base_metadata={"source": "manual.pdf"})

    for position, chunk in enumerate(chunks):
        assert chunk.metadata["source"] == "manual.pdf"
        assert chunk.metadata["chunk_index"] == position


def test_short_document_single_chunk() -> None:
    text = "# Tiny\n\nJust one sentence here."
    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].content == "Just one sentence here."
    assert chunks[0].metadata["heading_path"] == ["Tiny"]
