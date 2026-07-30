import datetime as dt

from pydantic import BaseModel


class DayCountResponse(BaseModel):
    day: dt.date
    conversations: int


class QuestionClusterResponse(BaseModel):
    question: str
    count: int
    variants: list[str]


class OverviewResponse(BaseModel):
    days: int
    total_conversations: int
    escalated_conversations: int
    deflected_conversations: int
    #: 0–1 fractions; the dashboard formats them as percentages.
    escalation_rate: float
    deflection_rate: float
    csat_average: float | None
    csat_responses: int
    thumbs_up: int
    thumbs_down: int
    conversations_over_time: list[DayCountResponse]
    top_unanswered: list[QuestionClusterResponse]


class QualityPointResponse(BaseModel):
    day: dt.date
    #: 0–1; the dashboard formats it as a percentage.
    faithfulness: float
    #: Answers scored that night. Shown so a 1.00 from two answers is not read as
    #: the same claim as a 1.00 from eighty.
    sample_size: int


class QualityResponse(BaseModel):
    """Latest online faithfulness for this org, plus a short trend (plan 6.6).

    Every score field is nullable on purpose. An org that has never been sampled
    has not scored zero — it has no measurement, and the card says so rather than
    rendering a 0% that reads as total failure.
    """

    latest_faithfulness: float | None
    latest_sample_size: int
    latest_at: dt.datetime | None
    baseline_faithfulness: float | None
    #: latest − baseline; None until there is history to compare against.
    delta: float | None
    #: The nightly job flagged a regression on the latest run.
    alerted: bool
    trend: list[QualityPointResponse]
