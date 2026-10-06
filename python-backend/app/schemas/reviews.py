"""Skemaer for anmeldelser: hvad API'et svarer med, og hvad "Send" modtager.

Svarskemaerne bestemmer præcis hvilke felter der forlader serveren. Et felt der ikke
står her, bliver aldrig sendt til browseren.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import CamelModel

# Googles egen grænse for længden af et svar på en anmeldelse.
MAX_REPLY_LENGTH = 4096


class ClientReview(CamelModel):
    """Én anmeldelse, som frontenden viser den. status: new = ingen udkast, ready = udkast, sent = besvaret."""

    id: UUID
    external_id: str
    reviewer_name: str
    rating: int | None
    review_text: str
    published_at: datetime | None
    status: Literal["new", "ready", "sent"]
    draft: str


class DashboardResponse(CamelModel):
    """Alt frontenden skal bruge for den indloggede brugers café: oplysninger og anmeldelser."""

    restaurant_id: UUID
    business_name: str
    business_type: str
    default_tone: str
    address: str
    rating: float | None
    user_rating_count: int | None
    reviews_fetched_at: datetime | None
    reviews: list[ClientReview]


class SendReplyRequest(CamelModel):
    """Det browseren sender til "Send": den tekst ejeren endte med (må ikke være tom)."""

    final_text: str = Field(min_length=1, max_length=MAX_REPLY_LENGTH)


class OkResponse(CamelModel):
    """Svaret {"ok": true}, når en handling er lykkedes."""

    ok: bool = True
