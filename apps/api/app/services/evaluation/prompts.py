"""Prompts used only by the offline evaluator.

Deliberately separate from ``app/agent/prompts.py``: the grounded contract there
is inviolable, and an evaluator that shared its prompts would be grading the
pipeline with the pipeline's own words. These two prompts are an independent
second opinion — a per-claim entailment check (RAGAS faithfulness) and reverse
question generation (RAGAS answer relevancy).

The marker constants are matched by ``OfflineGroundedProvider`` so an eval run
still produces deterministic scores with no model server; keep them in sync.
"""

from collections.abc import Sequence
from typing import Any

# Keep in sync with OfflineGroundedProvider.VERDICT_MARKER.
CLAIM_VERDICT_SYSTEM = """You check whether each claim is supported by the context.

You will be given numbered context passages and a numbered list of claims taken
from an answer. For every claim, decide whether the context directly supports it.

Reply with one line per claim, in order, formatted exactly as:
<claim number>: yes
<claim number>: no

"yes" only when the context states or clearly implies the claim. Use "no" if the
claim goes beyond the context, even slightly. Output nothing else."""

# Keep in sync with OfflineGroundedProvider.QUESTION_GEN_MARKER.
QUESTION_GEN_SYSTEM = """You generate questions that an answer responds to.

Given an answer, write the questions it would be a complete answer to. Output one
question per line, at most three, and nothing else."""


def build_claim_verdict_prompt(claims: Sequence[str], chunks: Sequence[dict[str, Any]]) -> str:
    context = "\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1))
    numbered = "\n".join(f"{i}: {claim}" for i, claim in enumerate(claims, start=1))
    return f"Context:\n{context}\n\nClaims:\n{numbered}"


def build_question_gen_prompt(answer: str) -> str:
    return f"Answer: {answer}"
