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
