"""Skemaer for statistiksiden: præcis de felter, der forlader serveren."""

from typing import Literal

from app.schemas.common import CamelModel


class MonthStats(CamelModel):
    """Én måned: antal anmeldelser skrevet i måneden og deres gennemsnit.

    `average_rating` er None, når der er for få anmeldelser til at gennemsnittet siger noget.
    """

    month: str  # "2026-10"
    count: int
    average_rating: float | None


class TopicStats(CamelModel):
    """Ét emne: hvor mange anmeldelser der nævner det, delt op efter stemning."""

    key: str
    label: str
    positive: int
    negative: int
    mixed: int
    total: int


class StatsResponse(CamelModel):
    """Alt statistiksiden viser for den indloggede brugers café."""

    business_name: str
    # Googles egen samlede score og antal, fra seneste hentning. Den rigtige score for caféen.
    google_rating: float | None
    google_rating_count: int | None
    # Tal ud fra de anmeldelser, vi har gemt (i dag højst nogle få, se README).
    review_count: int
    average_rating: float | None
    stars: list[int]  # antal med 1, 2, 3, 4 og 5 stjerner
    answered_count: int
    median_response_hours: float | None
    response_sample: int
    months: list[MonthStats]
    min_reviews_per_month: int
    topics: list[TopicStats]
    # Hvor langt mærkningen med emner er nået.
    analyzed_count: int
    pending_count: int
    topics_status: Literal["ready", "analyzing", "paused"]


class TopicsStartResponse(CamelModel):
    """Svaret på "mærk de nye anmeldelser": kører mærkningen nu?"""

    analyzing: bool
