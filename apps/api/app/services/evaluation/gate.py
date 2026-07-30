"""Threshold gate over an eval report (plan 6.4).

Pure arithmetic over a finished :class:`EvalReport`, deliberately separate from the
runner: the gate is what CI blocks a PR on, so it has to be readable, testable
without a model, and impossible to pass by accident.

Two rules the thresholds below follow:

- **A missing metric fails.** If a report has no ``faithfulness`` — every item
  errored, or a future refactor renames the key — the gate must not read that as
  "nothing exceeded the threshold". Silence is a failure, never a pass.
- **Refusal metrics come in pairs.** ``refusal_accuracy`` alone is trivially aced
  by refusing everything, so a gate that only checks it rewards a pipeline that
  answers nothing. ``false_refusal_rate`` is the other half and is reported
  alongside it; see ``docs/NOTES.md`` (Phase 6.3) for why this pipeline needs it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Bound = Literal["min", "max"]


@dataclass(frozen=True)
class Threshold:
    """One gate condition. ``advisory`` reports without failing the build."""

    metric: str
    bound: Bound
    value: float
    rationale: str
    advisory: bool = False

    def holds(self, score: float) -> bool:
        return score >= self.value if self.bound == "min" else score <= self.value


#: The plan's three gate conditions, plus the refusal counterpart as advisory.
#: false_refusal_rate is not a hard bound yet: 6.3 measured it at 0.40 on this
#: pipeline, so gating on it would fail every PR until retrieval and the serving
#: gate improve. It is surfaced so the number cannot be quietly ignored.
DEFAULT_THRESHOLDS: tuple[Threshold, ...] = (
    Threshold("faithfulness", "min", 0.85, "answers must be grounded in retrieved context"),
    Threshold("context_recall", "min", 0.70, "retrieval must surface the expected documents"),
    Threshold("refusal_accuracy", "min", 0.90, "out-of-KB questions must be refused"),
    Threshold(
        "false_refusal_rate",
        "max",
        0.25,
        "refusing answerable questions is the cost of refusing well",
        advisory=True,
    ),
)


@dataclass(frozen=True)
class GateCheck:
    threshold: Threshold
    #: None when the report carried no such metric — treated as a failure.
    score: float | None

    @property
    def passed(self) -> bool:
        return self.score is not None and self.threshold.holds(self.score)

    @property
    def blocking(self) -> bool:
        """Failed a threshold that is allowed to fail the build."""
        return not self.passed and not self.threshold.advisory


@dataclass(frozen=True)
class GateResult:
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return not any(check.blocking for check in self.checks)

    @property
    def failures(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.checks if check.blocking)


def evaluate_gate(
    metrics: dict[str, float | None], thresholds: Sequence[Threshold] = DEFAULT_THRESHOLDS
) -> GateResult:
    """Absent and explicitly-None metrics are the same thing here: not measured,
    therefore not certified, therefore a failure — but rendered as "not measured"
    rather than as a score of zero, which would misstate what the run found."""
    return GateResult(tuple(GateCheck(t, metrics.get(t.metric)) for t in thresholds))


def format_gate(result: GateResult) -> str:
    """A table CI logs. Every threshold is listed, passing or not — a gate that
    only prints failures leaves you guessing what it actually checked."""
    lines = [
        "",
        f"  {'metric':<22}{'score':>12}  {'bound':<12}  result",
        f"  {'-' * 56}",
    ]
    for check in result.checks:
        t = check.threshold
        score = "not measured" if check.score is None else f"{check.score:.3f}"
        bound = f"{'>=' if t.bound == 'min' else '<='} {t.value:.2f}"
        if check.passed:
            verdict = "pass"
        else:
            verdict = "ADVISORY" if t.advisory else "FAIL"
        lines.append(f"  {t.metric:<22}{score:>12}  {bound:<12}  {verdict}")
    lines.append("")
    if result.passed:
        advisories = [c for c in result.checks if not c.passed and c.threshold.advisory]
        lines.append("  gate PASSED" + (f" ({len(advisories)} advisory)" if advisories else ""))
    else:
        lines.append("  gate FAILED")
        for check in result.failures:
            score = "not measured" if check.score is None else f"{check.score:.3f}"
            lines.append(f"    {check.threshold.metric}={score} — {check.threshold.rationale}")
    for check in result.checks:
        if not check.passed and check.threshold.advisory:
            score = "not measured" if check.score is None else f"{check.score:.3f}"
            lines.append(
                f"    advisory: {check.threshold.metric}={score} — {check.threshold.rationale}"
            )
    return "\n".join(lines)
