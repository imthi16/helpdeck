"""Heading-aware, sentence-safe chunking.

Splits a document into chunks of roughly ``target_min``–``target_max`` tokens
with ~10–15% token overlap between consecutive chunks. Chunks never cross
heading boundaries and never split mid-sentence; the heading path of each
chunk is carried in its metadata.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import tiktoken

TARGET_MIN_TOKENS = 500
TARGET_MAX_TOKENS = 800
OVERLAP_RATIO = 0.12


@dataclass
class TextChunk:
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


@lru_cache
def _encoder() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
# Sentence boundary: terminal punctuation followed by whitespace and an
# uppercase letter/digit/quote, or a paragraph break.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class _Section:
    heading_path: tuple[str, ...]
    body: str


def _split_sections(text: str) -> list[_Section]:
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if body:
            sections.append(_Section(tuple(title for _, title in stack), body))

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
        else:
            buffer.append(line)
    flush()
    return sections


def _split_sentences(body: str) -> list[str]:
    sentences: list[str] = []
    for paragraph in re.split(r"\n{2,}", body):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        flat = re.sub(r"\s+", " ", paragraph)
        sentences.extend(s for s in _SENTENCE_END.split(flat) if s.strip())
    return sentences


def chunk_text(
    text: str,
    *,
    target_min: int = TARGET_MIN_TOKENS,
    target_max: int = TARGET_MAX_TOKENS,
    overlap_ratio: float = OVERLAP_RATIO,
    base_metadata: dict[str, Any] | None = None,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []

    for section in _split_sections(text):
        sentences = _split_sentences(section.body)
        if not sentences:
            continue
        token_counts = [count_tokens(sentence) for sentence in sentences]
        overlap_target = int(target_max * overlap_ratio)

        current: list[int] = []  # indexes into sentences
        current_tokens = 0
        index = 0
        while index < len(sentences):
            sentence_tokens = token_counts[index]
            if current and current_tokens + sentence_tokens > target_max:
                chunks.append(_emit(section, sentences, current, base_metadata))
                # Seed the next chunk with trailing sentences worth ~overlap_target tokens.
                overlap: list[int] = []
                overlap_tokens = 0
                for j in reversed(current):
                    if overlap_tokens + token_counts[j] > overlap_target or j == current[0]:
                        break
                    overlap.insert(0, j)
                    overlap_tokens += token_counts[j]
                current = overlap
                current_tokens = overlap_tokens
            current.append(index)
            current_tokens += sentence_tokens
            index += 1

        if current:
            # Merge a tiny tail into the previous chunk when it fits.
            if (
                chunks
                and chunks[-1].metadata.get("heading_path") == list(section.heading_path)
                and current_tokens < target_min // 4
                and chunks[-1].token_count + current_tokens <= target_max
            ):
                tail = " ".join(sentences[i] for i in current)
                merged = chunks[-1].content + " " + tail
                chunks[-1] = TextChunk(
                    content=merged,
                    token_count=count_tokens(merged),
                    metadata=chunks[-1].metadata,
                )
            else:
                chunks.append(_emit(section, sentences, current, base_metadata))

    for position, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = position
    return chunks


def _emit(
    section: _Section,
    sentences: list[str],
    indexes: list[int],
    base_metadata: dict[str, Any] | None,
) -> TextChunk:
    content = " ".join(sentences[i] for i in indexes)
    metadata: dict[str, Any] = dict(base_metadata or {})
    metadata["heading_path"] = list(section.heading_path)
    return TextChunk(content=content, token_count=count_tokens(content), metadata=metadata)
