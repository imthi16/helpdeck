"""Offline evaluation of the grounded RAG pipeline (plan 6.3).

Lives in ``app/`` rather than ``eval/`` because 6.5's nightly sampling job needs
the same scorers. ``eval/run_eval.py`` is the CLI on top of this package.
"""

from app.services.evaluation.dataset import GoldenItem, load_golden, select_subset
from app.services.evaluation.runner import EvalItemResult, EvalReport, run_eval

__all__ = [
    "EvalItemResult",
    "EvalReport",
    "GoldenItem",
    "load_golden",
    "run_eval",
    "select_subset",
]
