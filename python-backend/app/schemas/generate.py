"""Skemaer for POST /api/generate: hvad browseren må sende, og hvad den får tilbage.

Et skema beskriver hvordan data må se ud. FastAPI tjekker automatisk indkomne data
mod det, og er noget forkert, afvises kaldet med en fejl før resten af koden kører.
"""

from uuid import UUID

from pydantic import Field

from app.schemas.common import CamelModel

# Hver anmeldelse er et betalt kald til Claude, så et enkelt kald må ikke kunne
# bede om ubegrænset mange.
MAX_REVIEWS_PER_REQUEST = 25


class GenerateReview(CamelModel):
    """Én anmeldelse der skal have et udkast. Kun id'et bruges.

    Alt andet (navn, stjerner, tekst) læses fra databasen, så ingen kan få serveren til at
    sende opdigtet tekst videre til Claude under en anmeldelses navn.
    """

    id: UUID


class GenerateRequest(CamelModel):
    """Det browseren sender til /api/generate.

    Der er bevidst hverken café-navn eller café-id her. Hvilken café det gælder, ved serveren
    ud fra login, og navnet står i databasen.
    """

    tone: str = Field(default="", max_length=100)
    reviews: list[GenerateReview] = Field(min_length=1, max_length=MAX_REVIEWS_PER_REQUEST)


class GenerateResult(CamelModel):
    """Ét færdigt udkast i svaret."""

    id: UUID
    reviewer_name: str
    rating: int | None
    review_text: str
    draft: str
    model: str


class GenerateResponse(CamelModel):
    """Det /api/generate svarer med: en liste af udkast."""

    results: list[GenerateResult]
