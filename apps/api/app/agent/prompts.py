"""Agent prompts. The grounded contract lives here and is inviolable:
answer only from retrieved context, cite [n], otherwise refuse and escalate.
"""

from collections.abc import Sequence
from typing import Any

# Keep this marker in sync with OfflineGroundedProvider.ROUTER_MARKER.
ROUTER_SYSTEM = """Classify the user request into exactly one label:
- faq: a question that could be answered from a support knowledge base
- chitchat: greetings, thanks, or small talk with no support question
- human_request: the user explicitly asks to speak to a human, agent, or person

Respond with only the label: faq, chitchat, or human_request."""

REFUSAL_TEXT = "I don't have enough information to answer that."

GROUNDED_SYSTEM = f"""You are HelpDeck, a customer support assistant.

Answer the user's question using ONLY the numbered context passages provided.
- Cite every fact with its passage number in square brackets, like [1] or [2],
  immediately after the statement it supports.
- Use only the information in the context. Do not use outside knowledge.
- Do not invent citations or cite passages that are not provided.
- If the context does not contain the answer, reply with exactly:
  "{REFUSAL_TEXT}"

Be concise and helpful."""

# Keep "faithfulness" in this prompt in sync with OfflineGroundedProvider.JUDGE_MARKER.
JUDGE_SYSTEM = """You are a faithfulness judge for a grounded question-answering system.

Given an answer and the numbered context passages it was allowed to use, score
how well every claim in the answer is supported by that context. Return 1.0 if
every claim is fully supported, 0.0 if the answer contains unsupported claims or
hallucinations, and a value in between for partial support.

Respond with only a number between 0.0 and 1.0."""

CHITCHAT_REPLY = "Hi! I'm HelpDeck's assistant. Ask me anything about our products and policies."

HANDOFF_TEXT = (
    "I'm connecting you with a member of our support team who can help further. "
    "They'll follow up shortly."
)


def format_context(chunks: Sequence[dict[str, Any]]) -> str:
    lines = [f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)]
    return "\n".join(lines)


def build_answer_prompt(question: str, chunks: Sequence[dict[str, Any]]) -> str:
    return f"Context:\n{format_context(chunks)}\n\nQuestion: {question}"


def build_judge_prompt(answer: str, chunks: Sequence[dict[str, Any]]) -> str:
    return f"Context:\n{format_context(chunks)}\n\nAnswer: {answer}"
